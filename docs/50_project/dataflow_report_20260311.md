# Natsume AI 系统数据流全景报告

> 报告生成时间：2026-03-11 17:20（初版）| 最后更新：2026-03-17 18:19（P16 数据流图更新）
> 报告范围：双端通信 · 各功能数据流 · 各模块数据流 · Agent 治理 · Agent 路由 · Graph 编排 · 多智能体分派
> 报告模型：claude-4.6-sonnet
> 当前状态：P1-P16 全部完成，多智能体重构升级已封板（含 supervisor_dispatch / agent_dispatch / judge 完整链路）

---

## 目录

1. [系统整体架构概览](#一系统整体架构概览)
2. [双端通信数据流](#二双端通信数据流)
3. [主聊天链路数据流（SCENE_CHAT）](#三主聊天链路数据流scene_chat)
4. [复杂场景数据流（LangGraph 多节点）](#四复杂场景数据流langgraph-多节点)
5. [Agent 治理数据流（Governor）](#五agent-治理数据流governor)
6. [Agent 路由数据流（SceneRouter）](#六agent-路由数据流scenerouter)
7. [Graph 编排数据流（MainChain + LangGraph）](#七graph-编排数据流mainchain--langgraph)
8. [Retrieval 七路召回数据流](#八retrieval-七路召回数据流)
9. [情感层数据流](#九情感层数据流)
10. [Bond 关系层数据流](#十bond-关系层数据流)
11. [Edge 端各功能数据流](#十一edge-端各功能数据流)
12. [双端通信打通状态评估](#十二双端通信打通状态评估)
13. [多智能体分派数据流（P15/P16）](#十三多智能体分派数据流p15p16)

---

## 一、系统整体架构概览

```mermaid
graph TB
    subgraph USER["用户侧"]
        U1["用户语音输入"]
        U2["用户文字输入（微信/直接）"]
        U3["用户屏幕（截图）"]
    end

    subgraph EDGE["Edge Client（本地端）\nPort: 17800"]
        E_ASR["ASR 语音识别\nedge_client/senses/asr.py"]
        E_TTS["TTS 语音合成\nedge_client/actuators/tts_engine.py"]
        E_VTS["VTS 表情驱动\nedge_client/actuators/vts_driver.py"]
        E_WECHAT["微信监听\nedge_client/channels/wechat_listener.py"]
        E_DESKTOP["桌面控制\nedge_client/actuators/desktop_controller.py"]
        E_RPC["Edge RPC Gateway\nedge_client/app/fastapi_app.py"]
        E_FALLBACK["Fallback LLM\nedge_client/rpc/fallback_llm.py"]
        E_REPORTER["事件上报\nedge_client/rpc/event_reporter.py"]
        E_INPUT["输入上报\nedge_client/channels/input_reporter.py"]
    end

    subgraph HOST["Host Brain（云端/服务端）\nPort: 8000"]
        H_CHAT["POST /v1/chat\nchat_handler.py"]
        H_GOV["Governor 鉴权\ngovernor/decision.py"]
        H_ROUTER["SceneRouter\ngraph/main_chain.py"]
        H_RETRIEVAL["七路召回\nretrieval/orchestrator.py"]
        H_LLM["LLM Gateway\nintegrations/llm_gateway.py"]
        H_AIRLOCK["Airlock 检疫\nairlock/quarantine.py"]
        H_EPISODIC["情节记忆\nmemory/episodic_store.py"]
        H_BOND["Bond 关系层\nbond/bond_dynamics.py"]
        H_VTS_CTRL["VTS 控制\nchannels/vts.py"]
        H_EDGE_EVENTS["POST /v1/edge/events\nsystem_handler.py"]
        H_INPUT["POST /v1/host/input\ninput_routes.py"]
        H_AGENT["Agent Loop\napp/vision/agent_loop.py"]
    end

    subgraph LLM_BACKEND["LLM 后端"]
        LLM_LOCAL["本地 Ollama\nqwen3:8b\n127.0.0.1:11434"]
        LLM_CLOUD["云端 Gemini\ngemini-2.5-pro/flash"]
    end

    subgraph STORAGE["存储层"]
        DB_CHROMA["ChromaDB\n向量数据库"]
        DB_NEO4J["Neo4j / SQLite\nBond 关系图"]
        DB_EPISODIC["情节记忆存储"]
    end

    U1 --> E_ASR
    U2 --> E_WECHAT
    U3 --> E_DESKTOP

    E_ASR -->|"ASR 结果\nHTTP POST /v1/host/input"| H_INPUT
    E_WECHAT -->|"微信消息\nHTTP POST /v1/host/input"| H_INPUT
    E_REPORTER -->|"批量事件\nHTTP POST /v1/edge/events"| H_EDGE_EVENTS
    E_INPUT -->|"输入上报\nHTTP POST /v1/host/input"| H_INPUT

    H_CHAT -->|"SSE 流式响应"| E_RPC
    H_VTS_CTRL -->|"VTS 指令\nHTTP POST /v1/edge/vts"| E_RPC
    H_AGENT -->|"截图请求\nHTTP POST /v1/edge/screenshot"| E_RPC
    H_AGENT -->|"自动化操作\nHTTP POST /v1/edge/automate"| E_RPC

    E_RPC -->|"TTS 合成指令\nHTTP POST /v1/edge/speak"| E_TTS
    E_RPC -->|"VTS 表情指令"| E_VTS
    E_RPC -->|"截图结果"| H_AGENT
    E_RPC -->|"操作结果"| H_AGENT

    H_LLM -->|"本地推理"| LLM_LOCAL
    H_LLM -->|"云端推理"| LLM_CLOUD

    H_RETRIEVAL --> DB_CHROMA
    H_BOND --> DB_NEO4J
    H_EPISODIC --> DB_EPISODIC

    E_TTS --> U1
    E_VTS -->|"Live2D 表情"| U3
```

---

## 二、双端通信数据流

### 2.1 Host → Edge 通信（主动推送方向）

```mermaid
sequenceDiagram
    participant HOST as Host Brain<br/>:8000
    participant EDGE as Edge Client<br/>:17800

    Note over HOST,EDGE: 通道1：VTS 表情驱动（Host → Edge）
    HOST->>EDGE: POST /v1/edge/vts<br/>{action: "set_expression", emotion_tag: "HAPPY"}
    Note right of EDGE: HMAC 签名验证
    EDGE-->>HOST: {success: true, status: "ACCEPTED"}

    Note over HOST,EDGE: 通道2：TTS 语音合成（Host → Edge）
    HOST->>EDGE: POST /v1/edge/speak<br/>{text: "...", voice_id: "..."}
    EDGE-->>HOST: {success: true, status: "ACCEPTED"}

    Note over HOST,EDGE: 通道3：截图请求（Host → Edge，Agent Loop）
    HOST->>EDGE: POST /v1/edge/screenshot<br/>{monitor_index: 1}
    EDGE-->>HOST: {success: true, event: {image_b64: "...", width: 1920, height: 1080}}

    Note over HOST,EDGE: 通道4：桌面自动化（Host → Edge，Agent Loop）
    HOST->>EDGE: POST /v1/edge/automate<br/>{actions: [{type: "click", x: 100, y: 200}]}
    EDGE-->>HOST: {success: true, event: {success_count: 1, failed_count: 0}}

    Note over HOST,EDGE: 通道5：Fallback LLM（Host → Edge）
    HOST->>EDGE: POST /v1/edge/fallback/presence<br/>{request_id: "...", payload: {...}}
    EDGE-->>HOST: {success: true, status: "ACCEPTED"}
```

### 2.2 Edge → Host 通信（上报方向）

```mermaid
sequenceDiagram
    participant EDGE as Edge Client<br/>:17800
    participant HOST as Host Brain<br/>:8000

    Note over EDGE,HOST: 通道1：ASR 语音识别结果上报
    EDGE->>HOST: POST /v1/host/input<br/>{source: "ASR", text: "...", confidence: 0.95, is_final: true}
    HOST-->>EDGE: {success: true}

    Note over EDGE,HOST: 通道2：微信消息上报
    EDGE->>HOST: POST /v1/host/input<br/>{source: "WECHAT", from_wxid: "...", content: "..."}
    HOST-->>EDGE: {success: true}

    Note over EDGE,HOST: 通道3：批量事件上报（EDGE_TO_HOST）
    EDGE->>HOST: POST /v1/edge/events<br/>{events: [{event_type: "...", ts_ms: ..., session_id: "..."}]}
    HOST-->>EDGE: {success: true, accepted: N, rejected: 0}

    Note over EDGE,HOST: 通道4：微信 Hook 回调（本地 Hook 工具 → Edge）
    Note right of EDGE: wcf 等工具推送
    EDGE->>HOST: 内部转发（通过 WeChatListener）
```

### 2.3 双端 SSE 流式通信（核心聊天链路）

```mermaid
sequenceDiagram
    participant CLIENT as 客户端（前端/Edge）
    participant HOST as Host Brain<br/>POST /v1/chat

    CLIENT->>HOST: POST /v1/chat<br/>{message: "...", session_id: "...", emotion_tag: "..."}

    HOST-->>CLIENT: data: {"phase":"PRESENCE","event":"THINKING"}

    Note over HOST: Governor 鉴权（~20-50ms）
    HOST-->>CLIENT: data: {"phase":"PRESENCE","event":"GOV_APPROVED"}

    Note over HOST: 场景分类（pre_intent_classifier）
    alt SCENE_CHAT（日常对话）
        HOST-->>CLIENT: data: {"phase":"RENDER","chunk":"你好"}
        HOST-->>CLIENT: data: {"phase":"RENDER","chunk":"，"}
        HOST-->>CLIENT: data: {"phase":"SENTENCE","sentence":"你好，有什么可以帮你的？","sentence_index":0}
        HOST-->>CLIENT: data: {"phase":"COMMIT","done":true,"decision":"APPROVE","narrative":"..."}
    else 复杂场景（WORK_CODE/WORK_TRANSLATE/EXEC_INTENT）
        HOST-->>CLIENT: data: {"phase":"PRESENCE","event":"SCENE_ROUTED","scene_type":"SCENE_WORK_CODE"}
        HOST-->>CLIENT: data: {"phase":"GRAPH_START","graph_id":"host.chat.realtime"}
        HOST-->>CLIENT: data: {"phase":"GRAPH_NODE","node_id":"core_reasoner_primary","status":"running"}
        HOST-->>CLIENT: data: {"phase":"GRAPH_NODE","node_id":"workbench_code_assist","status":"done"}
        HOST-->>CLIENT: data: {"phase":"GRAPH_DONE","node_count":4}
        HOST-->>CLIENT: data: {"phase":"COMMIT","done":true,"decision":"APPROVE","narrative":"..."}
    end
```

---

## 三、主聊天链路数据流（SCENE_CHAT）

```mermaid
flowchart TD
    A["POST /v1/chat\nChatRequest"] --> B["handle_chat\nchat_handler.py"]
    B --> C["pre_intent_classifier\n关键词预分类"]
    C -->|"SCENE_CHAT"| D["_unified_chat_sse_generator"]

    D --> E["resolve_chat_emotion_context\nemotion_context.py"]
    E --> F["build_chat_governance_snapshot\n情感治理快照"]
    F --> G["Governor.evaluate_intent\ngovernor/decision.py\n~20-50ms"]

    G -->|"REJECT"| G1["COMMIT(REJECT)\n返回拒绝原因"]
    G -->|"APPROVE"| H["PRESENCE(GOV_APPROVED)"]

    H --> I["SceneRouter.route_sse\nmain_chain.py"]
    I -->|"SCENE_CHAT"| J["_sse_event_generator\nchat_stream_handler.py"]

    J --> K["并行启动 Retrieval\nasyncio.create_task"]
    K --> L["RetrievalOrchestrator.arun\n七路召回 + Rerank\n超时: 1200ms"]

    J --> M["resolve_host_llm_runtime_async\n路由矩阵决策"]
    M -->|"host_8b 健康"| M1["本地 qwen3:8b\nOllama :11434"]
    M -->|"host_8b 不健康"| M2["云端 Gemini/OpenAI"]

    L --> N["_build_stream_chat_prompt\n注入 Retrieval + Emotion 上下文"]
    M --> N

    N --> O["astream_llm_chunks\n流式 token 生成"]
    O --> P["RENDER(chunk) × N\ntoken 级 SSE 事件"]
    P --> Q["SentenceSplitter\n句子边界检测"]
    Q --> R["SENTENCE(sentence) × M\n句子级 SSE 事件"]

    R --> S["asyncio.gather 并行后处理"]
    S --> S1["Airlock.quarantine_ingress\n出口安全检疫"]
    S --> S2["persist_chat_episodic_memory\n情节记忆写入"]
    S --> S3["BondDynamicsWriter\nBond 积分更新"]
    S --> S4["_drive_stream_vts_emotion\nVTS 表情驱动"]

    S1 & S2 & S3 & S4 --> T["COMMIT(APPROVE)\n含 audit_refs 完整审计数据"]

    style G1 fill:#ff6b6b,color:#fff
    style M1 fill:#51cf66,color:#fff
    style M2 fill:#339af0,color:#fff
    style T fill:#51cf66,color:#fff
```

### 3.1 SCENE_CHAT 关键延迟分布

| 阶段 | 典型延迟 | 超时配置 |
|------|---------|---------|
| Governor 鉴权 | 20-50ms | 2000ms |
| Retrieval 七路召回 | 200-800ms | 1200ms（可配置） |
| LLM 首 token | 300-1500ms | 10000ms（最低） |
| 后处理并行（Airlock+Episodic+Bond+VTS） | 50-300ms | 各自独立超时 |

---

## 四、复杂场景数据流（LangGraph 多节点）

> **P15 重构说明**：所有复杂场景（WORK_CODE / WORK_TRANSLATE / EXEC_INTENT / AUTO）统一走 `PROFILE_WORKFLOW_SUPERVISED` 图，
> 旧的 `core_reasoner_primary → scene_router_post_core → workbench_*` 长链已废弃，
> 现在统一入口为 `supervisor_dispatch → agent_dispatch → judge_agent → chat_render_node`。

```mermaid
flowchart TD
    A["_unified_chat_sse_generator"] -->|"SCENE_WORK_CODE\nSCENE_WORK_TRANSLATE\nSCENE_EXEC_INTENT\nSCENE_TYPE_AUTO"| B["SceneRouter.route_sse\n→ _complex_scene_sse_generator"]

    B --> C["PRESENCE(SCENE_ROUTED)"]
    C --> D["resolve_host_llm_runtime_async\n强制云端 Gemini"]
    D --> E["build_chat_graph_config\nexecution_profile=PROFILE_WORKFLOW_SUPERVISED"]

    E --> F["GraphRuntimeContract.run_graph\nLangGraph 执行引擎"]
    F --> G["GRAPH_START 事件"]

    subgraph LANGGRAPH["LangGraph 节点执行（PROFILE_WORKFLOW_SUPERVISED）"]
        direction LR
        SD["supervisor_dispatch\nhandler: supervisor_dispatch_handler\n规则路由 or LLM 提案"]
        AD["agent_dispatch_node\ngraph_executor 直接分派\n根据 active_agent_id 动态路由"]
        AG["Agent Handler\nchat/code/translate/intent/retrieval"]
        JD["judge_agent_node\nhandler: judge_agent\nneed_judge=true 时执行"]
        CR["chat_render_node\n最终渲染输出"]
    end

    SD --> AD
    AD -->|"need_judge=true"| JD
    AD -->|"need_judge=false（快路径）"| CR
    AD --> AG
    AG --> AD
    JD --> CR

    F --> LANGGRAPH
    LANGGRAPH --> H["GRAPH_DONE 事件"]

    H --> I["asyncio.gather 并行后处理"]
    I --> I1["Airlock 检疫"]
    I --> I2["情节记忆写入"]
    I --> I3["Bond 更新"]
    I --> I4["VTS 表情驱动"]

    I1 & I2 & I3 & I4 --> J["COMMIT(APPROVE)\n含完整 audit_refs"]

    style SD fill:#339af0,color:#fff
    style AD fill:#cc5de8,color:#fff
    style AG fill:#51cf66,color:#fff
    style JD fill:#ffd43b,color:#333
    style CR fill:#51cf66,color:#fff
```

### 4.1 场景节点序列（P15 重构后）

| 场景 | execution_profile | 节点序列 | 总预算 |
|------|------------------|---------|--------|
| SCENE_CHAT | `PROFILE_CHAT_FAST` | `chat_direct_ja → chat_render_node` | 8-12s |
| SCENE_WORK_CODE | `PROFILE_WORKFLOW_SUPERVISED` | `supervisor_dispatch → agent_dispatch → [judge_agent] → chat_render_node` | 30-240s |
| SCENE_WORK_TRANSLATE | `PROFILE_WORKFLOW_SUPERVISED` | `supervisor_dispatch → agent_dispatch → [judge_agent] → chat_render_node` | 30-240s |
| SCENE_EXEC_INTENT | `PROFILE_INTENT_EXEC` | `intent_agent_entry → safety_verifier → judge_agent → chat_render_node` | 30-240s |
| SCENE_TYPE_AUTO | `PROFILE_WORKFLOW_SUPERVISED` | `supervisor_dispatch → agent_dispatch → [judge_agent] → chat_render_node` | 30-240s |

> **注**：`workbench_code_assist` / `workbench_translate` 仍注册为 model 节点，供 agent handler 内部通过 `invoke_model_node` 调用，不再作为主图边序列节点。

---

## 五、Agent 治理数据流（Governor）

```mermaid
flowchart TD
    A["raw_intent\n{action, type, args, nonce, ts_ms}"] --> B["IntentDecisionEngine.evaluate_intent\ngovernor/decision.py"]

    B --> C["S_INTENT_PARSE\n解析意图结构"]
    C --> D["S_INTENT_VALIDATE\n字段合法性校验"]
    D --> E["S_INTENT_REPLAY\nNonce 重放检测"]
    E --> F["S_INTENT_SEQ\n序列号单调性检查"]
    F --> G["S_INTENT_EXPIRED\n时间戳过期检查（±5min）"]
    G --> H["S_GOV_GATES\nR1-R7 规则门控"]

    subgraph GATES["R1-R7 规则门控"]
        R1["R1: 速率限制\nrate_limit_checker.py"]
        R2["R2: 会话状态检查"]
        R3["R3: 内容安全检查"]
        R4["R4: 权限级别检查"]
        R5["R5: 资源配额检查"]
        R6["R6: 意图白名单"]
        R7["R7: 工具调用权限"]
    end

    H --> GATES
    GATES --> I["S_GOV_EMOTION_GATE\nR8 情感门控"]

    I -->|"critical 状态 + TOOL_CALL/SKILL_EXEC"| J["❌ REJECT\nEMOTION_GATE_BLOCKED"]
    I -->|"通过"| K["S_INTENT_OK\n生成 intent_id"]

    K --> L["policy_cache 命中？\nP2-A 快速通道"]
    L -->|"命中（narrative_only）"| M["⚡ 快速通道\n跳过完整 gate 评估\n节省 20-30ms"]
    L -->|"未命中"| N["完整 gate 评估"]

    M & N --> O["DECISION_APPROVE\n{decision: 'APPROVE', intent_id: '...'}"]

    subgraph TIERED["M42 分层治理（Skill 注入后）"]
        T1["CACHE 通道\n低风险 Skill"]
        T2["RULE 通道\n中风险 Skill"]
        T3["AIRLOCK 通道\n高风险 Skill"]
    end

    O --> TIERED

    style J fill:#ff6b6b,color:#fff
    style M fill:#51cf66,color:#fff
    style O fill:#51cf66,color:#fff
```

### 5.1 Governor 决策链关键参数

| 门控 | 检查内容 | 失败原因码 |
|------|---------|-----------|
| R1 速率限制 | 每会话请求频率 | `RATE_LIMIT_EXCEEDED` |
| R5 资源配额 | Token 消耗配额 | `QUOTA_EXCEEDED` |
| R8 情感门控 | `critical` 状态下拒绝工具调用 | `EMOTION_GATE_BLOCKED` |
| Nonce 重放 | 防止请求重放攻击 | `NONCE_REPLAY` |
| 时间戳过期 | ±5 分钟窗口 | `INTENT_EXPIRED` |

---

## 六、Agent 路由数据流（SceneRouter）

```mermaid
flowchart TD
    A["_unified_chat_sse_generator\n鉴权通过后"] --> B["pre_intent_classifier\n关键词预分类"]

    B -->|"截图/屏幕/界面关键词"| C["SCENE_VISION_UI"]
    B -->|"代码/编程关键词\n（娱乐关键词不触发）"| D["SCENE_WORK_CODE"]
    B -->|"翻译关键词\n（无娱乐关键词时）"| E["SCENE_WORK_TRANSLATE"]
    B -->|"执行/安装/配置关键词\n（无娱乐关键词时）"| F["SCENE_EXEC_INTENT"]
    B -->|"娱乐关键词命中\n或其他"| G["SCENE_CHAT"]

    C & D & E & F & G --> H["scene_type 合法性校验\nALLOWED_EXPLICIT_SCENE_TYPES"]
    H -->|"非法 scene_type"| H1["降级为 SCENE_CHAT"]

    H --> I["SceneRouter.route_sse\nmain_chain.py"]

    I -->|"SCENE_VISION_UI"| J["_vision_ui_sse_generator\nchat_handler_vision.py"]
    I -->|"SCENE_CHAT + gaming + edge_enabled"| K["_gaming_edge_chat_sse_generator\nchat_handler_edge.py"]
    I -->|"SCENE_CHAT（普通）"| L["_sse_event_generator\nchat_stream_handler.py"]
    I -->|"复杂场景"| M["_complex_scene_sse_generator\nchat_handler_complex.py"]

    subgraph LLM_ROUTING["LLM 路由矩阵（resolve_host_llm_runtime）"]
        LR1["HOST_CHAT_FORCE_CLOUD=true\n→ 强制云端"]
        LR2["HOST_CHAT_HOST_8B_ENABLED=false\n→ 云端"]
        LR3["sys_mode=gaming\n→ 云端（Edge 3B）"]
        LR4["灰度桶 bucket < percent\n→ 本地 8B"]
        LR5["8B 健康探测通过\n→ 本地 8B（默认）"]
        LR6["8B 健康探测失败\n→ 云端 fallback"]
    end

    L --> LLM_ROUTING
    K --> LLM_ROUTING

    style J fill:#ffd43b,color:#333
    style K fill:#ff922b,color:#fff
    style L fill:#51cf66,color:#fff
    style M fill:#339af0,color:#fff
```

### 6.1 pre_intent_classifier 路由优先级

```
优先级（VISION_UI > WORK_CODE > WORK_TRANSLATE > EXEC_INTENT > CHAT）：
1. 截图/屏幕/界面关键词                → SCENE_VISION_UI（最高优先级）
2. 代码/编程关键词                     → SCENE_WORK_CODE（娱乐关键词不触发代码路由）
3. 娱乐关键词命中（动漫/音乐/游戏等）  → 强制 SCENE_CHAT（修复问题A：防止"安装这首歌"误判为 EXEC_INTENT）
4. 翻译关键词（无娱乐关键词时）        → SCENE_WORK_TRANSLATE
5. 执行/安装/配置关键词（无娱乐关键词时）→ SCENE_EXEC_INTENT
6. 其他（默认）                        → SCENE_CHAT
```

### 6.2 LLM 路由矩阵（resolve_host_llm_runtime）

```
1. HOST_CHAT_FORCE_CLOUD=true          → 强制云端（最高优先级）
2. HOST_CHAT_HOST_8B_ENABLED=false     → 云端
3. sys_mode=gaming                     → 云端（Edge 3B 路径）
4. 灰度排除（gray_rollout.in_rollout=false） → 云端
5. 本地 8B 健康探测通过                → 本地 qwen3:8b（默认路径）
6. 本地 8B 健康探测失败                → 云端 fallback
```

### 6.2 Gaming Edge 路由（特殊路径）

```mermaid
sequenceDiagram
    participant HOST as Host Brain
    participant EDGE as Edge Client<br/>:17800

    Note over HOST,EDGE: 条件：sys_mode=gaming AND HOST_GAMING_EDGE_CHAT_ENABLED=true AND HOST_ENABLE_EDGE_RPC=true

    HOST->>EDGE: POST /v1/edge/fallback/presence<br/>{request_id, payload: {message, emotion_context}}
    EDGE-->>HOST: {success: true, narrative: "..."}

    Note over HOST: Edge 失败时 fallback 到 _complex_scene_sse_generator
```

---

## 七、Graph 编排数据流（MainChain + LangGraph）

### 7.1 MainChain 节点链路（轻编排层）

```mermaid
flowchart LR
    subgraph MAIN_CHAIN["MainChain 主链路（前后置编排 + SSE 路由委托）"]
        N1["AirlockQuarantineNode\n超时: 1.0s"]
        N2["GovernorNode\n超时: 2.0s"]
        N3["GraphExecutorNode\n超时: 5.0s"]
        N4["RecallFusionNode\n超时: 5.0s"]
        N5["LlmGatewayNode\n超时: 30.0s"]
        N6["QAContractNode\n超时: 3.0s"]
        N7["EpisodicStoreNode\n超时: 2.0s"]
        N8["AuditNode\n超时: 1.0s"]
        N9["AirlockSanitizerNode\n超时: 1.0s"]
    end

    N1 --> N2 --> N3 --> N4 --> N5 --> N6 --> N7 --> N8 --> N9

    N2 -->|"governor_passed=true"| SR["SceneRouter.route\n子链分流"]
    SR -->|"TaskType.CHAT"| N3
    SR -->|"其他 TaskType"| SUB["注册的子链节点"]
```

> ✅ **当前状态（2026-03-11）**：`MainChain` 节点已补充最小可执行语义（输入净化、任务类型映射、召回/LLM/QA/审计/出口净化状态落点），
> `run_as_sse_adapter()` 已具备前置节点执行、SSE 事件观测、后置节点收尾，不再是纯透传。
> 对外 SSE 协议保持兼容，真实路由仍委托 `_unified_chat_sse_generator`。

### 7.2 graph_executor agent_dispatch_node 数据流（P15/P16）

```mermaid
flowchart TD
    A["supervisor_dispatch_node\nsupervisor_dispatch_handler\n写 payload['active_agent_id']\n写 supervisor_dispatch.need_judge"] --> B["graph_executor._run_single_step\nnode_id == 'agent_dispatch_node'"]

    B --> C["P16 active_agent_id 同步\npayload_active = payload['active_agent_id']\nresolved = payload_active or state_active\nstate = {**state, active_agent_id: resolved}"]

    C --> D["allowlist 校验\nfrom node_bindings['agent_dispatch_allowlist']\n6 个 agent: chat/code/translate/intent/judge/retrieval"]
    D -->|"不在 allowlist"| D1["DEGRADED\nAGENT_NOT_IN_ALLOWLIST\n→ edge_map 下一跳"]
    D -->|"通过"| E["_dispatch_to_agent(state)\n_AGENT_HANDLER_MAP[active_agent_id]"]

    E -->|"无 handler"| E1["DEGRADED\nAGENT_DISPATCH_NO_HANDLER\n→ edge_map 下一跳"]
    E -->|"有 handler"| F["_invoke_handler(handler_id)"]

    F --> G["读 supervisor_dispatch.need_judge"]
    G -->|"need_judge=true"| H["next_node = edge_map['agent_dispatch_node']\n→ judge_agent_node"]
    G -->|"need_judge=false"| I["next_node = CHAT_RENDER_NODE_ID\n快路径：跳过 judge"]

    H & I --> J["返回新 state\n{payload, trace, pending_node_id,\nagent_artifacts, active_agent_id}"]

    style D1 fill:#ff6b6b,color:#fff
    style E1 fill:#ff6b6b,color:#fff
    style I fill:#51cf66,color:#fff
    style J fill:#51cf66,color:#fff
```

### 7.3 supervisor_dispatch_handler 决策流（P15）

```mermaid
flowchart TD
    A["supervisor_dispatch_handler\n{scene_type, user_input}"] --> B["读取 SUPERVISOR_MODE 环境变量"]

    B -->|"SUPERVISOR_MODE=rule（默认）"| C["规则路由\n_rule_based_dispatch(scene_type)"]
    B -->|"SUPERVISOR_MODE=llm"| D["LLM 提案模式\n_llm_based_dispatch(user_input, gateway)"]

    C --> C1["scene_type → agent_id 映射\nWORK_CODE → code_agent\nWORK_TRANSLATE → translate_agent\nEXEC_INTENT → intent_agent\n其他 → chat_agent"]
    C1 --> C2["supervisor_dispatch\n{supervisor_mode: 'rule'\nsupervisor_decision_source: 'rule'\nneed_judge: false/true}"]

    D --> D1{"gateway 可用？"}
    D1 -->|"否"| D2["回退 rule 模式\nsupervisor_decision_source: 'llm_fallback_rule'"]
    D1 -->|"是"| D3["gateway.complete(prompt)\n→ JSON 解析\n{selected_agent, reason, risk_mode, need_judge}"]
    D3 -->|"解析失败"| D2
    D3 -->|"解析成功"| D4["supervisor_dispatch\n{supervisor_mode: 'llm'\nsupervisor_decision_source: 'llm'\nneed_judge: true/false}"]

    C2 & D2 & D4 --> E["写入 payload['active_agent_id']\n写入 payload['supervisor_dispatch']\n返回 result"]

    style D2 fill:#ffd43b,color:#333
    style D4 fill:#51cf66,color:#fff
    style E fill:#51cf66,color:#fff
```

### 7.4 LangGraph 图配置（build_chat_graph_config / _build_graph_config_by_profile）

> **P15 重构说明**：`build_chat_graph_config` 现在通过 `execution_profile` 选择图结构，不再按 `scene_type` 直接映射节点链路。

```mermaid
flowchart TD
    A["build_chat_graph_config\ngraph_config_builder.py"] --> B["resolve_execution_profile\n按 scene_type 解析"]

    B -->|"SCENE_CHAT\n→ PROFILE_CHAT_FAST"| C["Chat 快速图\nchat_direct_ja → chat_render_node\n超时: max(chat_timeout, 10000ms)\nmax_output_tokens=200"]

    B -->|"SCENE_WORK_CODE / WORK_TRANSLATE / AUTO\n→ PROFILE_WORKFLOW_SUPERVISED"| D["Supervised 图\nsupervisor_dispatch → agent_dispatch_node\n→ judge_agent_node → chat_render_node"]
    D --> D1["agent_dispatch_allowlist\n[chat/code/translate/intent/judge/retrieval]"]
    D --> D2["workbench_code_assist / workbench_translate\n注册为 model 节点（供 handler 内部调用）"]

    B -->|"SCENE_EXEC_INTENT\n→ PROFILE_INTENT_EXEC"| E["Intent 图\nintent_agent_entry → safety_verifier\n→ judge_agent_node → chat_render_node"]

    subgraph NODE_MODELS["节点模型分配"]
        M1["chat_direct_ja → 本地 qwen3:8b（PROFILE_CHAT_FAST）"]
        M2["core_reasoner_primary → gemini-2.5-pro"]
        M3["core_reasoner_secondary → gemini-2.5-flash（fallback）"]
        M4["workbench_* → gemini-2.5-pro（model 节点，不入主图边序列）"]
        M5["consistency_verifier → gemini-2.5-flash-lite"]
        M6["renderer_ja_c_style / chat_render_node → gemini-2.5-flash"]
        M7["safety_verifier → gemini-2.5-flash-lite"]
    end

    subgraph POLICY_TIMEOUT["Policy Timeout 预算"]
        PT1["PROFILE_CHAT_FAST → 8-12s"]
        PT2["PROFILE_WORKFLOW_SUPERVISED → 30-240s"]
        PT3["PROFILE_INTENT_EXEC → 30-240s"]
    end
```

---

## 八、Retrieval 七路召回数据流

```mermaid
flowchart TD
    A["RetrievalOrchestrator.arun\nrequest: {query, session_id, top_k, emotion_context}"] --> B["RetrievalPipelineContract.run\nretrieval/pipeline.py"]

    B --> C["RecallFusion\nretrieval/recall_fusion.py"]

    subgraph RECALL_ROUTES["七路召回（并行执行）"]
        R1["Route 1: 向量语义召回\nChromaDB 嵌入检索"]
        R2["Route 2: 全文关键词召回\nfull_text_searcher.py"]
        R3["Route 3: 情节记忆召回\nEpisodicStore"]
        R4["Route 4: Bond 关系召回\nBondScoreState"]
        R5["Route 5: 知识图谱召回\nNeo4j"]
        R6["Route 6: 情感上下文召回\nEmotionTimeline"]
        R7["Route 7: 工具元数据召回\nToolCatalog"]
    end

    C --> RECALL_ROUTES

    RECALL_ROUTES --> D["HybridFusion\nhybrid_fusion.py\nRRF 融合排序"]
    D --> E["Reranker\nreranker.py\nQwen3-Reranker-0.6B"]
    E --> F["EvidencePack\nevidence_pack.py\n最终证据包"]

    F --> G["RetrievalOrchestratorResult\n{retrieval_summary, retrieval_evidence_items}"]

    subgraph CACHE["结果缓存（可选）"]
        C1["缓存键: session_id+query+top_k+intent_id+emotion_context"]
        C2["TTL: 3000ms（默认）"]
        C3["最大条目: 256"]
    end

    G --> CACHE
    CACHE -->|"缓存命中"| H["直接返回（跳过召回）"]

    G --> I["注入 prompt_builder\n构建 LLM 上下文"]

    style E fill:#339af0,color:#fff
    style F fill:#51cf66,color:#fff
```

### 8.1 Retrieval 降级策略

| 状态 | 触发条件 | 处理方式 |
|------|---------|---------|
| `SKIPPED` | `HOST_ENABLE_RETRIEVAL=false` 或 `HOST_CHAT_DISABLE_RETRIEVAL=true` | 跳过召回，使用空摘要 |
| `DEGRADED(RETRIEVAL_TIMEOUT)` | 超过 `HOST_STREAM_RETRIEVAL_TIMEOUT_MS`（默认 1200ms） | 使用降级摘要，后台任务继续 |
| `DEGRADED(RETRIEVAL_FAILED)` | 召回管道异常 | 使用降级摘要 |
| `OK` | 正常完成 | 注入完整证据包 |

---

## 九、情感层数据流

```mermaid
flowchart TD
    A["ChatRequest\n{mood_state, emotion_tag, emotion_context}"] --> B["resolve_chat_emotion_context\nemotion_context.py"]

    B --> C["normalize_hard_state\nmetabolism.py\n硬状态规范化"]
    C --> D["build_hard_snapshot\nHardStateSnapshot\n{allowed_emotion_tags, emotion_range_hint}"]

    B --> E["EmotionVector 创建\nVAD 三维模型（已修复 #6）"]
    E --> F["EmotionTimeline 记录\n情感时间线"]
    F --> G["compute_emotion_echo\n情感回声计算"]
    G --> H["evaluate_emotion_triggers\n情感触发器评估"]

    D & H --> I["emotion_context 字典\n{emotion_tag, emotion_intensity, vad_vector, ...}"]

    I --> J["build_chat_governance_snapshot\n治理快照"]
    J --> K["Governor R8 情感门控\ncritical 状态拦截"]

    I --> L["adjust_gen_params_for_emotion\nllm_helpers.py\ntemperature/top_p 动态调整"]

    I --> M["build_chat_emotion_snapshot\n注入 prompt payload"]

    I --> N["_drive_stream_vts_emotion\nVTS 表情驱动\n仅 SCENE_CHAT（已修复 #7 扩展到复杂场景）"]
    N --> O["resolve_vts_expression\nchannels/vts.py\n16 种情感 → 表情 ID 映射"]
    O --> P["POST /v1/edge/vts\nEdge 端表情更新"]

    I --> Q["BondDynamicsWriter.classify_interaction\n互动分类（MINIMAL/ENGAGED/DEEP）"]
    Q --> R["BondStageEngine.apply_event\n情感强度加权（>0.7 → 1.5x）"]
    R --> S["BondEmotionalTexture.update\n亲密温度更新（已修复 #5）"]

    style K fill:#ff6b6b,color:#fff
    style P fill:#51cf66,color:#fff
    style S fill:#51cf66,color:#fff
```

### 9.1 情感标签映射（16 种）

| 情感标签 | 中文 | VTS 表情 ID |
|---------|------|------------|
| HAPPY | 开心 | exp_happy |
| SAD | 悲伤 | exp_sad |
| ANGRY | 愤怒 | exp_angry |
| SURPRISED | 惊讶 | exp_surprised |
| NEUTRAL | 平静 | exp_neutral |
| ... | ... | ... |

---

## 十、Bond 关系层数据流

```mermaid
flowchart TD
    A["用户交互（每次对话）"] --> B["BondDynamicsWriter.classify_interaction\nbond/bond_dynamics.py"]

    B -->|"文本长度 < 20 且情感强度 < 0.3"| C["MINIMAL 互动\n+1 积分"]
    B -->|"文本长度 20-100 或情感强度 0.3-0.7"| D["ENGAGED 互动\n+3 积分"]
    B -->|"文本长度 > 100 或情感强度 > 0.7 或含问号"| E["DEEP 互动\n+5 积分"]

    C & D & E --> F["BondStageEngine.apply_event\nbond/bond_stage_engine.py"]

    F --> G["情感强度加权\n> 0.7 → 1.5x 积分"]
    G --> H["BondEmotionalTexture.update\n亲密温度 = 回复节奏 × 情感均值（已修复 #5）"]
    H --> I["BondScoreState 更新\n{score, stage, texture}"]

    I --> J{阶段晋级检查}
    J -->|"score >= 阶段阈值"| K["阶段晋级\n陌生人→相识→朋友→挚友→知己→灵魂伴侣"]
    J -->|"未达阈值"| L["保持当前阶段"]

    K & L --> M["BondScoreState.to_summary\n全中文语义输出\n{stage_cn, style_hint, repair_hint}"]
    M --> N["注入 ContextPack\n影响 LLM 生成风格"]

    subgraph RIFT["裂痕-修复双轨"]
        RF1["CONFLICT 事件 → 裂痕积累"]
        RF2["APOLOGY/COMFORT 事件 → 裂痕修复"]
        RF3["repair_hint 注入 prompt"]
    end

    F --> RIFT

    subgraph STORAGE["持久化（已修复 #10）"]
        S1["HOST_BOND_BACKEND=sqlite → SqliteBondClient"]
        S2["HOST_BOND_BACKEND=neo4j → Neo4jBondClient"]
        S3["默认: InMemoryNeo4jBondClient（进程重启丢失）"]
    end

    I --> STORAGE

    style K fill:#ffd43b,color:#333
    style N fill:#51cf66,color:#fff
```

---

## 十一、Edge 端各功能数据流

### 11.1 ASR 语音识别数据流

```mermaid
flowchart LR
    A["麦克风输入"] --> B["ASR 引擎\nedge_client/senses/asr.py"]
    B --> C["语音识别结果\n{text, confidence, is_final, seq}"]
    C --> D["EdgeInputReporter.report_asr\nchannels/input_reporter.py"]
    D -->|"HTTP POST /v1/host/input"| E["Host Brain\ninput_routes.py"]
    E --> F["触发 POST /v1/chat\n或直接处理"]
    D -->|"失败时"| G["本地缓存\n最多 500 条\n后台重试（最多 5 次）"]
```

### 11.2 TTS 语音合成数据流

```mermaid
flowchart LR
    A["Host Brain\nSSE SENTENCE 事件"] --> B["Edge RPC Gateway\nPOST /v1/edge/speak"]
    B --> C["VoicePipeline.enqueue_speak\nactuators/speech.py"]
    C --> D["TTS Engine\nactuators/tts_engine.py"]
    D --> E["音频输出\n扬声器播放"]
    B -->|"barge_in"| F["VoicePipeline.barge_in\n打断当前播放"]
```

### 11.3 VTS 表情驱动数据流

```mermaid
flowchart LR
    A["Host Brain\n_drive_stream_vts_emotion"] --> B["POST /v1/edge/vts\n{action: 'set_expression', emotion_tag: 'HAPPY'}"]
    B --> C["Edge RPC Gateway\n_handle_vts"]
    C --> D["VTSDriver.set_expression\nactuators/vts_driver.py"]
    D --> E["VTube Studio API\nWebSocket 连接"]
    E --> F["Live2D 模型\n表情更新"]
```

### 11.4 Agent Loop 视觉自动化数据流

```mermaid
flowchart TD
    A["POST /v1/agent/run\nagent_routes.py"] --> B["AgentLoop.run\napp/vision/agent_loop.py"]

    B --> C["EdgeRpcClient.capture_screenshot\napp/vision/edge_rpc_client.py"]
    C -->|"POST /v1/edge/screenshot"| D["Edge 端截图\nDesktopController.capture_screenshot"]
    D --> C

    C --> E["ActionPlanner.plan\napp/vision/action_planner.py"]
    E --> F["LLM 视觉分析\n分析截图内容"]
    F --> G["ActionPlan\n{actions: [{type, x, y, text}]}"]

    G --> H["EdgeRpcClient.execute_plan\n转换为 DesktopAction"]
    H -->|"POST /v1/edge/automate"| I["Edge 端执行\nDesktopController.execute_actions"]
    I --> H

    H --> J["UIAssertion.verify\napp/vision/ui_assertion.py"]
    J -->|"验证失败"| B
    J -->|"验证通过"| K["AgentLoop 完成"]
```

---

## 十二、双端通信打通状态评估

### 12.1 通信通道完整性矩阵

| 通信通道 | 方向 | 协议 | 鉴权 | 实现状态 | 打通状态 |
|---------|------|------|------|---------|---------|
| `/v1/chat` SSE 流式 | 客户端→Host | HTTP SSE | 无（内网） | ✅ 完整实现 | ✅ **已打通** |
| `/v1/edge/speak` TTS | Host→Edge | HTTP POST | HMAC | ✅ 完整实现 | ✅ **已打通** |
| `/v1/edge/vts` 表情 | Host→Edge | HTTP POST | HMAC | ✅ 完整实现 | ✅ **已打通** |
| `/v1/edge/barge_in` 打断 | Host→Edge | HTTP POST | HMAC | ✅ 完整实现 | ✅ **已打通** |
| `/v1/edge/screenshot` 截图 | Host→Edge | HTTP POST | HMAC | ✅ 完整实现 | ✅ **已打通** |
| `/v1/edge/automate` 自动化 | Host→Edge | HTTP POST | HMAC | ✅ 完整实现 | ✅ **已打通** |
| `/v1/edge/fallback/*` Fallback | Host→Edge | HTTP POST | HMAC | ✅ 完整实现 | ✅ **已打通** |
| `/v1/host/input` 输入上报 | Edge→Host | HTTP POST | HMAC | ✅ 完整实现 | ✅ **已打通**（`run_main_chain_from_input` 已实现，触发完整 chat 流程） |
| `/v1/edge/events` 事件上报 | Edge→Host | HTTP POST | 无 | ✅ 完整实现 | ✅ **已打通**（情感事件触发 Bond 更新 + VTS 驱动，本次修复） |
| `/v1/edge/wechat/callback` 微信 | 本地Hook→Edge | HTTP POST | 无（本地） | ✅ 完整实现 | ✅ **已打通** |

### 12.2 未完全打通的关键问题

```mermaid
flowchart TD
    subgraph FIXED["✅ 本次修复已打通的链路"]
        F1["Edge 事件上报\nPOST /v1/edge/events\n✅ 情感事件触发 Bond 更新\n✅ 情感事件触发 VTS 驱动\n（ASR_FINAL/EMOTION_DETECTED/USER_REACTION）"]

        F2["ASR/微信输入上报\nPOST /v1/host/input\n✅ run_main_chain_from_input 已实现\n✅ 触发完整 Governor+LLM+Bond+VTS 流程"]
    end

    subgraph REMAIN["⚠️ 仍存在的限制"]
        B3["Gaming Edge 路径\nPOST /v1/edge/fallback/presence\n⚠️ 无 VTS 驱动（有意设计）\n⚠️ 无 Bond 更新（有意设计）\n⚠️ 无 episodic 写入（有意设计）\n已补充设计说明注释"]

        B4["SCENE_VISION_UI\n⚠️ 已可用但需 image_upload_consent=true 才允许传图\n⚠️ 仍需持续验证真实云模型稳定性"]
    end

    style F1 fill:#51cf66,color:#fff
    style F2 fill:#51cf66,color:#fff
    style B3 fill:#ffd43b,color:#333
    style B4 fill:#ffd43b,color:#333
```

### 12.3 双端 SSE 事件协议对齐状态

| SSE 事件 | SCENE_CHAT | 复杂场景 | Gaming Edge | 对齐状态 |
|---------|-----------|---------|------------|---------|
| `PRESENCE(THINKING)` | ✅ | ✅ | ✅ | ✅ 对齐 |
| `PRESENCE(GOV_APPROVED)` | ✅ | ✅ | ✅ | ✅ 对齐 |
| `PRESENCE(SCENE_ROUTED)` | ❌（不发送） | ✅ | ❌ | ⚠️ 不对称（设计如此） |
| `GRAPH_START` | ❌ | ✅ | ❌ | ⚠️ 仅复杂场景 |
| `RENDER(chunk)` | ✅ | ❌ | ❌ | ⚠️ 仅 SCENE_CHAT |
| `SENTENCE(sentence)` | ✅ | ❌ | ❌ | ⚠️ 仅 SCENE_CHAT |
| `GRAPH_DONE` | ❌ | ✅ | ❌ | ⚠️ 仅复杂场景 |
| `COMMIT(APPROVE)` | ✅ | ✅ | ✅ | ✅ 对齐 |
| `COMMIT.audit_refs.vts` | ✅ | ✅（已修复 #12） | ⚠️ 空 `{}` | ⚠️ Gaming Edge 缺失 |
| `COMMIT.audit_refs.context_pack_validation` | ✅ | ✅ | ⚠️ 空 `{}` | ⚠️ Gaming Edge 缺失 |

---

## 十三、多智能体分派数据流（P15/P16）

### 13.1 _AGENT_HANDLER_MAP 与 AGENT_ALLOWLIST 对齐状态（P16 后）

| agent_id | `_AGENT_HANDLER_MAP` | `AGENT_ALLOWLIST` | handler 文件 | need_judge | 说明 |
|----------|---------------------|-------------------|-------------|-----------|------|
| `chat_agent` | ✅ | ✅ | `chat_agent_handler.py` | false | 快路径，跳过 judge |
| `code_agent` | ✅ | ✅ | `code_agent_handler.py` | true | P9 新增 |
| `translate_agent` | ✅ | ✅ | `translate_agent_handler.py` | true | P10 新增 |
| `intent_agent` | ✅ | ✅ | `intent_agent_handler.py` | true | 高风险 |
| `judge_agent` | ✅ | ✅ | `judge_agent_handler.py` | — | 裁判节点 |
| `retrieval_agent` | ✅ | ✅ | `retrieval_agent_handler.py` | false | P11 新增，P16 补注册 |
| `safety_agent` | ❌ | ❌ | 无 | — | 待 P17+ 补充 handler |
| `renderer_agent` | ❌ | ❌ | 无 | — | 待 P17+ 补充 handler |

### 13.2 多智能体完整链路时序

```mermaid
sequenceDiagram
    participant SR as SceneRouter
    participant SD as supervisor_dispatch_node
    participant GE as graph_executor
    participant AD as agent_dispatch_node
    participant AG as Agent Handler
    participant JD as judge_agent_node
    participant CR as chat_render_node

    SR->>SD: state {scene_type, user_input}
    SD->>SD: SUPERVISOR_MODE=rule → 规则路由
    SD->>SD: SUPERVISOR_MODE=llm → LLM 提案（失败回退 rule）
    SD-->>GE: payload['active_agent_id'] = 'code_agent'
    SD-->>GE: payload['supervisor_dispatch'] = {need_judge: true, ...}

    GE->>AD: node_id == 'agent_dispatch_node'
    AD->>AD: P16 同步 active_agent_id 到 state
    AD->>AD: allowlist 校验（6 个 agent）
    AD->>AG: _dispatch_to_agent → _invoke_handler('code_agent_handler')
    AG-->>AD: agent_artifacts

    alt need_judge=true
        AD->>JD: next_node = judge_agent_node
        JD-->>CR: 裁判通过后 → chat_render_node
    else need_judge=false（快路径）
        AD->>CR: next_node = CHAT_RENDER_NODE_ID（跳过 judge）
    end

    CR-->>SR: GRAPH_DONE
```

---

*报告最后更新：2026-03-17 18:21 | 生成模型：claude-4.6-sonnet*
*基于代码库深度分析，覆盖 host_brain 和 edge_client 全部核心模块，含 P16 多智能体技术债清理*
*P15 重构后数据流图已全量更新：图4/图6/图7.4 对齐 PROFILE_WORKFLOW_SUPERVISED 新链路*

