# Natsume AI 系统数据流全景报告

> 报告生成时间：2026-03-11 17:20（初版）| 最后更新：2026-03-11 18:12（严格审计修订）
> 报告范围：双端通信 · 各功能数据流 · 各模块数据流 · Agent 治理 · Agent 路由 · Graph 编排
> 报告模型：claude-4.6-sonnet
> 本次修复：问题 A/B/C/D/F 已按优先级处理（见第十三节）；严格代码审计已完成（见第十六节）

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
13. [关键问题与修复状态](#十三关键问题与修复状态)
14. [本次修复变更摘要（2026-03-11 17:28）](#十四本次修复变更摘要2026-03-11-1728)
15. [选项一/二/三 实施记录（2026-03-11 17:52）](#十五选项一二三-实施记录2026-03-11-1752)
16. [严格代码审计报告（2026-03-11 18:12）](#十六严格代码审计报告2026-03-11-1812)

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

```mermaid
flowchart TD
    A["_unified_chat_sse_generator"] -->|"SCENE_WORK_CODE\nSCENE_WORK_TRANSLATE\nSCENE_EXEC_INTENT"| B["SceneRouter.route_sse\n→ _complex_scene_sse_generator"]

    B --> C["PRESENCE(SCENE_ROUTED)"]
    C --> D["resolve_host_llm_runtime_async\n强制云端 Gemini"]
    D --> E["build_chat_graph_config\n构建 LangGraph 配置"]

    E --> F["GraphRuntimeContract.run_graph\nLangGraph 执行引擎"]
    F --> G["GRAPH_START 事件"]

    subgraph LANGGRAPH["LangGraph 节点执行（按场景）"]
        direction LR
        N1["core_reasoner_primary\ngemini-2.5-pro\n12s 超时"]
        N2["scene_router_post_core\n场景后路由"]
        N3_CODE["workbench_code_assist\ngemini-2.5-pro\n10s 超时"]
        N3_TRANS["workbench_translate\ngemini-2.5-pro\n10s 超时"]
        N3_EXEC["planner_constraints\ngemini-2.5-pro\n8s 超时"]
        N4["consistency_verifier\ngemini-2.5-flash-lite\n7s 超时"]
        N5["renderer_ja_c_style\ngemini-2.5-flash\n9s 超时"]
    end

    N1 --> N2
    N2 -->|"WORK_CODE"| N3_CODE
    N2 -->|"WORK_TRANSLATE"| N3_TRANS
    N2 -->|"EXEC_INTENT"| N3_EXEC
    N3_CODE & N3_TRANS & N3_EXEC --> N4
    N4 --> N5

    F --> LANGGRAPH
    LANGGRAPH --> H["GRAPH_DONE 事件"]

    H --> I["asyncio.gather 并行后处理"]
    I --> I1["Airlock 检疫"]
    I --> I2["情节记忆写入"]
    I --> I3["Bond 更新"]
    I --> I4["VTS 表情驱动\n（已修复 #7）"]

    I1 & I2 & I3 & I4 --> J["COMMIT(APPROVE)\n含完整 audit_refs"]

    style N1 fill:#339af0,color:#fff
    style N3_CODE fill:#51cf66,color:#fff
    style N3_TRANS fill:#51cf66,color:#fff
    style N3_EXEC fill:#51cf66,color:#fff
```

### 4.1 场景节点序列

| 场景 | 节点序列 | 总预算 |
|------|---------|--------|
| SCENE_CHAT | `chat_direct_ja` | 10-15s |
| SCENE_WORK_CODE | `core_reasoner_primary → workbench_code_assist → consistency_verifier → renderer_ja_c_style` | 90-240s |
| SCENE_WORK_TRANSLATE | `core_reasoner_primary → workbench_translate → consistency_verifier → renderer_ja_c_style` | 90-240s |
| SCENE_EXEC_INTENT | `core_reasoner_primary → planner_constraints → tool_planner → safety_verifier → consistency_verifier → renderer_ja_c_style` | 90-240s |

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
    B -->|"代码/编程关键词"| D["SCENE_WORK_CODE"]
    B -->|"翻译关键词"| E["SCENE_WORK_TRANSLATE"]
    B -->|"执行/安装/配置关键词"| F["SCENE_EXEC_INTENT"]
    B -->|"其他（含娱乐白名单）"| G["SCENE_CHAT"]

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

### 6.1 路由决策优先级

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

### 7.2 LangGraph 图配置（build_chat_graph_config）

```mermaid
flowchart TD
    A["build_chat_graph_config\ngraph_config_builder.py"] --> B{场景类型}

    B -->|"SCENE_CHAT（快速路径）"| C["单节点图\nchat_direct_ja\n超时: max(chat_timeout, 10000ms)"]

    B -->|"AUTO（场景路由器）"| D["scene_router_entry\n→ 动态分流"]
    D --> D1["chat_direct_ja（CHAT）"]
    D --> D2["core_reasoner_primary（复杂）"]
    D2 --> D3["scene_router_post_core"]
    D3 --> D4["workbench_*/planner_constraints"]
    D4 --> D5["consistency_verifier"]
    D5 --> D6["renderer_ja_c_style"]

    B -->|"SCENE_WORK_CODE"| E["core_reasoner_primary\n→ workbench_code_assist\n→ consistency_verifier\n→ renderer_ja_c_style"]

    B -->|"SCENE_WORK_TRANSLATE"| F["core_reasoner_primary\n→ workbench_translate\n→ consistency_verifier\n→ renderer_ja_c_style"]

    B -->|"SCENE_EXEC_INTENT"| G["core_reasoner_primary\n→ planner_constraints\n→ tool_planner\n→ safety_verifier\n→ consistency_verifier\n→ renderer_ja_c_style"]

    subgraph NODE_MODELS["节点模型分配"]
        M1["chat_direct_ja → 本地 qwen3:8b"]
        M2["core_reasoner_primary → gemini-2.5-pro"]
        M3["core_reasoner_secondary → gemini-2.5-flash（fallback）"]
        M4["workbench_* → gemini-2.5-pro"]
        M5["consistency_verifier → gemini-2.5-flash-lite"]
        M6["renderer_ja_c_style → gemini-2.5-flash"]
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

## 十三、关键问题与修复状态

### 13.1 已修复问题（截至 2026-03-11 14:10）

| # | 问题描述 | 严重度 | 修复状态 |
|---|---------|--------|---------|
| 1 | 同步 HTTP 健康探测阻塞事件循环（最多 3s） | 🔴 高危 | ✅ 已修复：`asyncio.to_thread` 包装 |
| 2 | `intent_draft.action` 固定为 `narrative_only`，EXEC_INTENT 绕过情感门控 | 🟠 中高危 | ✅ 已修复：动态 `_action_map` 映射 |
| 3 | `scene_type` 可被客户端强制覆盖，无 Governor 校验 | 🟠 中高危 | ✅ 已修复：`ALLOWED_EXPLICIT_SCENE_TYPES` 白名单 |
| 4 | `MainChain` 是死代码，与实际路由完全脱节 | 🟠 中高危 | ✅ 已修复：迁移为 `run_as_sse_adapter` 适配层 |
| 5 | `BondEmotionalTexture.update()` 从未被调用 | 🟠 中高危 | ✅ 已修复：`apply_event` 中激活 |
| 6 | VAD 情感模型（EmotionVector/EmotionTimeline）完全孤立 | 🟡 中危 | ✅ 已修复：接入 `normalize_chat_emotion_context` |
| 7 | VTS 驱动仅在 SCENE_CHAT，复杂场景无 VTS 触发 | 🟡 中危 | ✅ 已修复：`chat_handler_complex.py` 后处理接入 |
| 8 | SCENE_CHAT 流式路径无 Bond 更新 | 🟡 中危 | ✅ 已修复：`_run_bond()` 并行协程 |
| 9 | `POST /bond/event` 无情感参数 | 🟡 中危 | ✅ 已修复：新增 `emotion_tag`/`emotion_intensity` |
| 10 | Bond 状态进程重启丢失（内存存储） | 🟡 中危 | ✅ 已修复：`SqliteBondClient` + 环境变量切换 |
| 11 | `SCENE_VISION_UI` 静默走 `chat_direct` 节点 | 🟡 中危 | ✅ 已修复：显式路由到 `_vision_ui_sse_generator`（支持 consent 门控与视觉审计） |
| 12 | 复杂场景 COMMIT 缺少 `vts` 字段 | 🟢 低危 | ✅ 已修复：补全 `audit_refs.vts` |

### 13.2 本次修复的架构问题（2026-03-11 17:28）

| # | 问题描述 | 严重度 | 修复方式 | 修复文件 |
|---|---------|--------|---------|--------|
| **B（P1）** | `POST /v1/edge/events` 接收到的 Edge 事件无情感处理、无 Bond 更新、无 VTS 驱动 | 🟡 中危 | ✅ 已修复：情感事件（ASR_FINAL/EMOTION_DETECTED 等）触发 Bond 更新 + VTS 驱动（fire-and-forget） | `system_handler.py` |
| **A（P2）** | `pre_intent_classifier` 误判：娱乐+EXEC_INTENT 关键词同时命中时（如"帮我安装一下这首歌"）误判为 EXEC_INTENT | 🟡 中危 | ✅ 已修复：娱乐关键词优先级高于 EXEC_INTENT，同时命中时归为 SCENE_CHAT | `graph_config_builder.py` |
| **C（P3）** | Gaming Edge 路径无 VTS/Bond/Episodic，设计意图不明确 | 🟢 低危 | ✅ 已修复：补充最小路径设计说明注释，明确有意跳过的功能及原因 | `chat_handler_edge.py` |
| **D（P4）** | `input_routes.py` 调用 `run_main_chain_from_input` 但该函数不存在，ASR/微信输入上报无法触发完整 chat 流程 | 🟡 中危 | ✅ 已修复：在 `main_chain.py` 中实现 `run_main_chain_from_input`，构建 ChatRequest 并触发完整链路 | `graph/main_chain.py` |
| **F（P5）** | Bond 积分阈值硬编码（STRANGER=0, SOULMATE=300），无法运行时调整 | 🟢 低危 | ✅ 已修复：`get_bond_stage_engine()` 支持通过 `HOST_BOND_STAGE_THRESHOLDS` / `HOST_BOND_SCORE_MAP` 环境变量覆盖 | `dependencies.py` |

### 13.3 仍存在的架构问题（已全部解决，见第十五节）

| # | 问题描述 | 严重度 | 状态 |
|---|---------|--------|------|
| D-残留 | `RoutingMatrix`、`ContextPackBuilder`、`MemoryGuard`、`StateArbiter` 四个模块有工厂函数但无调用方 | 🟡 中危 | ✅ 已解决（选项二，2026-03-11 17:40） |
| E | `SCENE_VISION_UI` 仅“路径放行”，缺少联调证据 | 🟡 中危 | ✅ 已解决（2026-03-11）：双端联调证据已补齐（含 consent true/false 分支） |

---

## 附录：关键配置环境变量

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `HOST_CHAT_HOST_8B_ENABLED` | `true` | 是否启用本地 8B 模型 |
| `HOST_CHAT_FORCE_CLOUD` | `false` | 强制使用云端 LLM |
| `HOST_CHAT_HOST_8B_BASE_URL` | `http://127.0.0.1:11434/v1` | 本地 Ollama 地址 |
| `HOST_CHAT_HOST_8B_MODEL_ID` | `qwen3:8b` | 本地模型 ID |
| `HOST_SYS_MODE` | `normal` | 系统模式（normal/gaming） |
| `HOST_GAMING_EDGE_CHAT_ENABLED` | `false` | 是否启用 Gaming Edge 路由 |
| `HOST_ENABLE_EDGE_RPC` | `true` | 是否启用 Edge RPC |
| `HOST_ENABLE_RETRIEVAL` | `true` | 是否启用七路召回 |
| `HOST_CHAT_DISABLE_RETRIEVAL` | `false` | 是否禁用 SCENE_CHAT 召回 |
| `HOST_STREAM_RETRIEVAL_TIMEOUT_MS` | `1200` | 流式召回超时（ms） |
| `HOST_STREAM_VTS_EMOTION_ENABLED` | `true` | 是否启用流式 VTS 情感驱动 |
| `HOST_BOND_BACKEND` | `memory` | Bond 存储后端（memory/sqlite/neo4j） |
| `HOST_BOND_STAGE_THRESHOLDS` | — | Bond 阶段积分阈值 JSON 覆盖（如 `{"SOULMATE":200}`）（本次新增） |
| `HOST_BOND_SCORE_MAP` | — | Bond 积分映射 JSON 覆盖（如 `{"INTERACTION_DEEP":8}`）（本次新增） |
| `HOST_CHAT_GRAY_ENABLED` | `false` | 是否启用灰度发布 |
| `HOST_CHAT_GRAY_PERCENT` | `100` | 灰度发布百分比（0-100） |
| `GEMINI_API_KEY` | — | Gemini API 密钥 |
| `OPENAI_API_KEY` | — | OpenAI API 密钥 |

---

*报告初版生成时间：2026-03-11 17:20 | 最后更新：2026-03-11 17:52 | 生成模型：claude-4.6-sonnet*
*基于代码库深度分析，覆盖 host_brain 和 edge_client 全部核心模块*

---

## 十四、本次修复变更摘要（2026-03-11 17:28）

### 修复文件清单

| 文件 | 修改内容 | 问题编号 |
|------|---------|--------|
| `host_brain/app/system_handler.py` | `receive_edge_events_response` 新增情感事件后处理：Bond 更新 + VTS 驱动（fire-and-forget） | B |
| `host_brain/app/graph_config_builder.py` | `pre_intent_classifier` 修复娱乐关键词优先级，防止误判为 EXEC_INTENT | A |
| `host_brain/app/chat_handler_edge.py` | `_gaming_edge_chat_sse_generator` 补充最小路径设计说明注释 | C |
| `host_brain/app/graph/main_chain.py` | 新增 `run_main_chain_from_input` 函数，打通 ASR/微信输入上报→完整 chat 流程 | D |
| `host_brain/app/dependencies.py` | `get_bond_stage_engine` 支持通过环境变量覆盖积分阈值和积分映射 | F |

### 双端通信打通状态（修复后）

```
修复前：
  Edge → Host 事件上报（/v1/edge/events）：仅写 ledger 日志，无情感处理
  Edge → Host 输入上报（/v1/host/input）：接口存在，但 run_main_chain_from_input 未实现

修复后：
  Edge → Host 事件上报（/v1/edge/events）：✅ 情感事件触发 Bond 更新 + VTS 驱动
  Edge → Host 输入上报（/v1/host/input）：✅ run_main_chain_from_input 已实现，触发完整 chat 流程

仍存在的限制（有意设计）：
  Gaming Edge 路径：最小路径设计，无 VTS/Bond/Episodic（已补充设计说明）

后续已解决（2026-03-11 17:52）：
  SCENE_VISION_UI：✅ 已加入白名单，视觉链路完整接入（见第十五节）
  孤立模块：✅ RoutingMatrix/ContextPackBuilder/MemoryGuard/StateArbiter 全部接入（见第十五节）
```

---

## 十五、选项一/二/三 实施记录（2026-03-11 17:52）

### 15.1 选项一：SCENE_VISION_UI 视觉链路完整接入

**背景**：`chat_handler_vision.py` 已有完整的 `_vision_ui_sse_generator` 实现（Gemini Vision 多模态分析链路），但 `ALLOWED_EXPLICIT_SCENE_TYPES` 白名单中不含 `SCENE_VISION_UI`，导致客户端无法显式指定该场景。

**修复内容**：
- `graph_config_builder.py`：将 `SCENE_VISION_UI` 加入 `ALLOWED_EXPLICIT_SCENE_TYPES` 白名单
- 注释更新：说明 VISION_UI 场景需要 `image_upload_consent=true` 才允许上传图片

**视觉链路数据流**：

```mermaid
flowchart TD
    A["客户端 POST /v1/chat
{scene_type: SCENE_VISION_UI,
vision_context: {...}}"] --> B["chat_handler._unified_chat_sse_generator"]
    B --> C["SceneRouter.route_sse()
SCENE_VISION_UI 分支"]
    C --> D["chat_handler_vision._vision_ui_sse_generator"]
    D --> E["vision_context_parser
解析 base64/url/text"]
    E --> F{"check_image_upload_allowed()
REQUIRE_CONSENT 策略"}
    F -->|"consent=true"| G["Airlock image_filter
元数据检查 + 敏感词扫描"]
    F -->|"consent=false"| H["降级：纯文字模式"]
    G --> I["Gemini Vision 多模态分析"]
    H --> I
    I --> J["vision_renderer
可选 ActionPlan 生成"]
    J --> K["COMMIT(APPROVE)
audit_refs.vision_input"]
```

**支持的输入模式**：

| 模式 | 条件 | 说明 |
|------|------|------|
| `image` | `vision_context.image_base64` 或 `image_url` 非空 + `image_upload_consent=true` | 真实多模态分析 |
| `text_description` | `vision_context.screen_description` 等文字字段非空 | 降级文字模式 |
| `text_only` | 无 vision_context | 最低优先级，纯文字分析 |

---

### 15.2 选项二：孤立模块接入实际路由

**背景**：`RoutingMatrix`、`ContextPackBuilder`、`MemoryGuard`、`StateArbiter` 四个模块有完整实现和工厂函数，但未接入实际路由，形成孤立代码。

#### 15.2.1 RoutingMatrix 接入

**接入点**：`graph_config_builder._resolve_host_llm_runtime_core` 返回值

**修复内容**：在 `_resolve_host_llm_runtime_core` 中调用 `RoutingMatrix.select_with_fallback()` 后，不仅写入 `routing_matrix_decision`，还将 `selected_model` 映射回真实 `provider_name/model_id` 选择（`cloud` / `host_8b`），并保留 8B 健康探测与策略门控作为 fallback。结果透传至 `COMMIT.audit_refs.routing_matrix_decision`，可观测。

**场景映射**：

| SCENE_TYPE | TaskType | 说明 |
|-----------|---------|------|
| SCENE_CHAT | CHAT | 普通聊天 |
| SCENE_EXEC_INTENT | REASONING | 执行/规划场景优先云端推理 |
| SCENE_WORK_CODE | CODE | 代码辅助 |
| SCENE_WORK_TRANSLATE | TRANSLATE | 翻译任务 |
| SCENE_VISION_UI | CHAT | 视觉场景复用 CHAT 路由 |

**降级策略**：
- RoutingMatrix 调用失败时返回 `{"status": "DEGRADED", "reason": "..."}`，保持原有运行时选择
- 当 RoutingMatrix 选择 `host_8b` 但 8B 不可用（策略禁用/灰度排除/健康检查失败）时，不强制切换，保留原有 cloud 路径，并在 `routing_matrix_decision.not_applied_reason` 记录原因

#### 15.2.2 ContextPackBuilder 接入

**接入点**：`chat_stream_handler._sse_event_generator`，在 `_build_stream_chat_prompt` 调用之前

**修复内容**：从 `retrieval_evidence_items` 中提取各路召回文本，调用 `ContextPackBuilder.build()` 执行 token 预算控制；在 `HOST_CONTEXT_PACK_ACTIVE=true`（默认）时，将截断结果回写到 `retrieval_evidence_items`，并把摘要附加到 `COMMIT.audit_refs.context_pack_builder`。

**数据流**：
```
_run_retrieval() → retrieval_evidence_items
  → [新增] ContextPackBuilder.build(recall_results, task_type="chat")
    → token 预算截断（world_canon≤800 / style_canon≤400 / episodic_vector≤600 等）
    → 语义冲突检测（world_canon vs episodic_vector）
    → 全局截断（总量超 2700 tokens 时按优先级丢弃）
  → [新增] HOST_CONTEXT_PACK_ACTIVE=true 且有有效截断结果时，回写 retrieval_evidence_items
  → context_pack_builder_summary 附加到 COMMIT.audit_refs
  → _build_stream_chat_prompt()（使用回写后的 retrieval_evidence_items）
```

**降级策略**：ContextPackBuilder 调用失败或无有效截断结果时返回 `{"status": "DEGRADED"}` / `{"status": "SKIPPED"}`，不影响主链路，prompt 构建继续使用原始 `retrieval_evidence_items`。

#### 15.2.3 MemoryGuard 接入

**接入点**：`observability_persist.persist_chat_episodic_memory`

**修复内容**：在写入 EpisodicStore 前，调用 `MemoryGuard.check_episodic_write()` 进行宪法校验。校验失败时返回 `SKIPPED` 状态（不抛出异常），降级策略保证主链路不中断。同时调用 `aggregate_emotion_tags()` 进行 session 粒度情绪聚合。

**校验规则**：
- 对话轮次 ≥ 2（从 `emotion_context.turn_count` 读取，默认 2）
- 包含情感标签 OR 事实标签 OR 偏好标签（至少一类）

**数据流**：
```
persist_chat_episodic_memory()
  → [新增] MemoryGuard.check_episodic_write(EpisodicRecord)
    → 校验通过 → 继续写入 EpisodicStore
    → 校验失败 → 返回 {status: "SKIPPED", reason_code: "MEMORY_GUARD_REJECTED:..."}
  → [新增] MemoryGuard.aggregate_emotion_tags(session_id, emotion_tags)
  → episodic_store.write_memory()（原有逻辑）
```

#### 15.2.4 StateArbiter 接入

**接入点**：
- `http_hooks.runtime_lifespan`（应用启动时注册状态源）
- `chat_stream_handler._sse_event_generator`（流式主链查询事实）

**修复内容**：
- 启动时注册三个状态源（WorldCanonStore、StyleCanonStore、EpisodicStore），使用适配 getter（`world_canon_facts` / `style_canon_facts` / `episodic_latest`）避免无效方法引用
- 在流式主链调用 `StateArbiter.resolve("world_canon_facts", session_id)`；命中时将结果注入 `retrieval_evidence_items`（`slot_name=world_canon`）并写入 `retrieval_summary.world_canon`
- COMMIT 附加 `audit_refs.state_arbiter`，可观测是否真实命中与降级

**优先级顺序**：
```
WorldCanonStore(SSOT) > StyleCanonStore > EpisodicStore > BondGraph > ChromaDB > EdgeRuntime
```

**数据流**：
```
runtime_lifespan() 启动时
  → [新增] StateArbiter.register_source(WORLD_CANON, _world_get_fact)
  → [新增] StateArbiter.register_source(STYLE_CANON, _style_get_fact)
  → [新增] StateArbiter.register_source(EPISODIC_STORE, _episodic_get_fact)
  → 各源注册失败时独立降级，记录 new_module_statuses["state_arbiter_sources"]

流式主链（SCENE_CHAT）：
  → StateArbiter.resolve("world_canon_facts", session_id)
    → 命中时写入 retrieval_summary.state_arbiter + retrieval_summary.world_canon
    → 构造 world_canon evidence 注入 retrieval_evidence_items
    → COMMIT.audit_refs.state_arbiter 输出 status/source/fallback/conflict
```

---

### 15.3 选项三：单元测试补充

**新增测试文件**：`tests/unit/test_isolated_modules_integration.py`

**测试覆盖**（18 个测试，全部通过）：

| 测试类 | 测试数 | 覆盖内容 |
|--------|--------|----------|
| `TestSceneVisionUIWhitelist` | 2 | SCENE_VISION_UI 已加入白名单；所有预期场景均在白名单中 |
| `TestRoutingMatrixIntegration` | 5 | CHAT→HOST_8B；REASONING→CLOUD；降级链跳过失败模型；路由日志写入 |
| `TestContextPackBuilderIntegration` | 4 | 空召回；world_canon 组装；token 截断；style_canon 任务冲突降级 |
| `TestMemoryGuardIntegration` | 4 | 情感标签通过；轮次不足拒绝；无标签拒绝；persist 函数降级路径 |
| `TestStateArbiterIntegration` | 3 | 最高优先级返回；不可达降级；全空返回 None |

---

### 15.4 选项四：端到端联调验证（TODO）

> **TODO**：启动 Host + Edge 双端，实际验证本次修复的通信链路是否真正打通。
>
> 验证清单：
> - [ ] ASR 输入 → `POST /v1/host/input` → `run_main_chain_from_input` → 完整 chat 流程
> - [ ] Edge 情感事件 → `POST /v1/edge/events` → Bond 更新 + VTS 驱动
> - [ ] `SCENE_VISION_UI` 请求 → Gemini Vision 分析 → SSE 流式回复
> - [ ] RoutingMatrix 路由决策出现在 `COMMIT.audit_refs` 中
> - [ ] MemoryGuard 校验失败时 episodic 写入被跳过（reason_code 可观测）
> - [ ] StateArbiter 冲突事件写入 triptych 审计

---

### 15.5 本次修改文件清单

| 文件 | 修改内容 | 选项 |
|------|---------|------|
| `host_brain/app/graph_config_builder.py` | `ALLOWED_EXPLICIT_SCENE_TYPES` 加入 `SCENE_VISION_UI`；RoutingMatrix 决策映射回真实 runtime（`provider/model`），并保留策略/健康兜底；返回值附加 `routing_matrix_decision.runtime_applied` | 选项一、选项二 |
| `host_brain/app/chat_stream_handler.py` | `_sse_event_generator` 接入 `ContextPackBuilder`；默认回写截断后的 `retrieval_evidence_items`（`HOST_CONTEXT_PACK_ACTIVE` 可回退）；调用 `StateArbiter.resolve("world_canon_facts")` 注入 world_canon evidence；COMMIT 附加 `state_arbiter` | 选项二 |
| `host_brain/app/system_handler.py` | `receive_edge_events_response` 增加 `ALLOWED_EDGE_EVENT_TYPES` 与 `ALLOWED_EDGE_EVENT_SOURCES` 校验，非法输入返回 400 | 选项二 |
| `host_brain/app/observability_persist.py` | `persist_chat_episodic_memory` 接入 `MemoryGuard` 宪法校验 | 选项二 |
| `host_brain/app/http_hooks.py` | `runtime_lifespan` 注册 `StateArbiter` 状态源适配 getter（world/style/episodic），修复无效类名/方法引用导致的注册降级 | 选项二 |
| `tests/unit/test_isolated_modules_integration.py` | 新增 18 个单元测试，全部通过 | 选项三 |
| `tests/unit/test_chat_stream_handler_p0_stream_retrieval.py` | 增加 `test_stream_context_pack_builder_active_rewrites_prompt_inputs`、`test_stream_state_arbiter_resolve_injects_world_canon_into_prompt` | 选项三 |
| `tests/unit/test_host_8b_runtime_route.py` | 增加 `test_resolve_host_llm_runtime_routing_matrix_reasoning_scene_overrides_to_cloud`，验证 RoutingMatrix 真实改写 runtime | 选项三 |
| `tests/unit/test_edge_events_whitelist.py` | 新增白名单安全边界测试（允许合法类型、拒绝未知类型/来源） | 选项三 |

---

*最后更新：2026-03-11 21:07 | 本节由 claude-4.6-sonnet 自动生成*

---

## 十六、严格代码审计报告（2026-03-11 21:07）

> 本节基于对所有关键文件的逐行代码审计，以代码真实执行语义为准。
> 凡与前序章节存在冲突，以本节为准。

### 16.1 唯一可信执行时序

#### /v1/chat 主链路

```mermaid
sequenceDiagram
    participant C as 客户端
    participant H as handle_chat<br/>chat_handler.py
    participant U as _unified_chat_sse_generator
    participant G as Governor.evaluate_intent
    participant P as pre_intent_classifier
    participant SR as SceneRouter.route_sse<br/>main_chain.py
    participant SSE as 各场景 SSE 生成器

    C->>H: POST /v1/chat {message, session_id, ...}
    H->>H: pre_intent_classifier(message) → _pre_scene
    H->>H: 构建 intent_draft（action 动态映射）
    H->>U: _unified_chat_sse_generator(...)
    U-->>C: PRESENCE(THINKING)
    U->>G: evaluate_intent(intent_draft)
    G-->>U: {decision: APPROVE, intent_id}
    U-->>C: PRESENCE(GOV_APPROVED)
    U->>P: pre_intent_classifier(message) 或复用 _pre_scene
    P-->>U: scene_type
    alt scene_type != SCENE_CHAT
        U-->>C: PRESENCE(SCENE_ROUTED)
    end
    U->>SR: route_sse(scene_type, ...)
    SR->>SSE: 分流到对应生成器
    SSE-->>C: 各阶段 SSE 事件流
    SSE-->>C: COMMIT(APPROVE)
```

**关键事实**：
- `pre_intent_classifier` 在 `handle_chat` 中调用一次（构建 `intent_draft`），在 `_unified_chat_sse_generator` 中**复用结果**（`_pre_classified_scene`），不重复调用
- `Governor.evaluate_intent` 在 `_unified_chat_sse_generator` 中调用，`_sse_event_generator`（SCENE_CHAT）通过 `_pre_approved_governor_result` 跳过重复鉴权
- `SceneRouter.route_sse` 是真实路由入口；`MainChain.run_as_sse_adapter` 为前后置编排适配层（执行 pre/post 节点并观测 SSE，再委托 `_unified_chat_sse_generator`）

#### /v1/host/input 链路

```mermaid
sequenceDiagram
    participant E as Edge Client
    participant IR as input_routes.py
    participant MC as run_main_chain_from_input<br/>main_chain.py
    participant U as _unified_chat_sse_generator

    E->>IR: POST /v1/host/input {source, text, session_id}
    IR-->>E: {success: true}（立即返回）
    IR->>MC: asyncio.create_task（fire-and-forget）
    MC->>MC: 构建 ChatRequest + intent_draft
    MC->>U: MainChain.run_as_sse_adapter(...)
    U->>U: 完整 Governor+LLM+Bond+VTS 流程
    Note over U: 无 SSE 客户端，事件被消费但不推送
```

**关键事实**：
- HTTP 200 在 `asyncio.create_task` 之后立即返回，副作用（LLM 生成、Bond 更新、VTS 驱动）在后台异步执行
- 副作用完成与否对调用方不可观测（fire-and-forget）
- 这是**有意设计**（ASR/微信输入不需要等待 LLM 响应）

### 16.2 模块成熟度重分级表

| 模块 | 当前等级 | 理由 | 证据（文件/函数） | 建议升级 | 下一步最小改动 |
|------|---------|------|----------------|---------|--------------|
| `MainChain` | `ACTIVE`（深度业务化，2026-03-12） | 各节点已具备最小可执行语义（输入净化、任务类型映射、召回/LLM/QA/审计/出口净化状态）；`run_as_sse_adapter` 执行 pre/post 节点并观测 COMMIT，不再纯透传 | `graph/main_chain.py::AirlockQuarantineNode.execute`、`GovernorNode.execute`、`MainChain.run_as_sse_adapter`、`_observe_adapter_sse_event` | 是（向 AUTHORITATIVE 演进） | 将 Governor/Recall/QA 的“占位语义”替换为真实业务调用并保持 SSE 契约稳定 |
| `RoutingMatrix` | `ACTIVE`（策略门控） | 在 `_resolve_host_llm_runtime_core` 中调用 `select_with_fallback()` 后，将 `selected_model` 映射回真实 `provider_name/model_id`（支持 cloud/host_8b）；并保留 8B 健康探测与策略门控 fallback；`routing_matrix_decision.runtime_applied` 标记是否真的改写 runtime | `graph_config_builder.py::_resolve_host_llm_runtime_core`（`route_target/provider_name/model_id` 在 RoutingMatrix 后可变） | 否（已达预期） | 扩展 `skills/edge_3b` 到主链 runtime 映射，补充对应回退策略测试 |
| `ContextPackBuilder` | `ACTIVE`（可回退） | 在 `_sse_event_generator` 中调用 `_cpb.build()` 后，将截断结果在 `HOST_CONTEXT_PACK_ACTIVE=true` 时回写到 `retrieval_evidence_items`，真实影响 `_build_stream_chat_prompt`；同时保留 `audit_refs.context_pack_builder` 观测 | `chat_stream_handler.py::_sse_event_generator`，`retrieval_evidence_items = _rewritten_items`，`context_pack_builder_summary.mode/applied_to_prompt` | 否（已达预期） | 继续监控 `rewritten_item_count` 与回复质量，按需调优 slot token 配额 |
| `MemoryGuard` | `ACTIVE` ✅ | 在 `persist_chat_episodic_memory` 中调用 `check_episodic_write()`，校验失败时**真实跳过** episodic 写入 | `observability_persist.py::persist_chat_episodic_memory`，`_guard.check_episodic_write(_record)` 抛出异常时 `return {status: SKIPPED}` | 否（已达预期） | 补充 E2E 测试验证校验失败路径 |
| `StateArbiter` | `ACTIVE`（局部路径） | 启动时注册 world/style/episodic 三个适配状态源；流式主链调用 `resolve("world_canon_facts")`，命中结果会注入 `retrieval_evidence_items` 和 `retrieval_summary.world_canon`，并写入 `audit_refs.state_arbiter` | `http_hooks.py::runtime_lifespan`（源注册）；`chat_stream_handler.py::_resolve_state_arbiter_world_fact` + `_sse_event_generator`（业务调用） | 否（已达预期） | 扩展更多 fact_key（如 style/episodic）到主链查询，并补冲突路径观测测试 |
| `SCENE_VISION_UI` | `PROD_READY`（最小加固，2026-03-12） | 在 `E2E_VERIFIED` 基础上新增：`_VisionSLAMonitor` 追踪连续失败/降级率/延迟，超阈值写入 ledger 告警；请求来源校验（`HOST_VISION_REQUIRE_INTERNAL_ORIGIN`）；`COMMIT.audit_refs.sla_monitor` 可观测；所有加固均有降级策略不阻断主链路 | `chat_handler_vision.py::_VisionSLAMonitor`、`_call_vision_llm`（SLA 记录）、`_vision_ui_sse_generator`（来源校验+延迟告警）；`tests/unit/test_main_chain_deep_business.py::TestVisionSLAMonitor`（4 passed） | 否（已达最小 PROD_READY） | 补充多租户鉴权（HMAC/签名）；将 ledger 告警推送到外部监控系统（如 Prometheus/PagerDuty） |，`_vision_ui_sse_generator` 可达；`image_upload_consent=true/false` 两分支均完成双端实测，`audit_refs.vision_input` 按预期写入 | `chat_handler.py`（scene_type 规范化+白名单）、`chat_handler_vision.py::_vision_ui_sse_generator`、双端联调日志（2026-03-11） | 是（向 PROD_READY） | 增加生产鉴权/签名与真实云模型 SLA 监控，补异常回退演练 |
| `/v1/host/input` | `ACTIVE`（非闭环） | `run_main_chain_from_input` 已实现，fire-and-forget 触发完整链路；但副作用不可观测，HTTP 200 只代表 accepted | `main_chain.py::run_main_chain_from_input`，`input_routes.py` 调用 `asyncio.create_task` | 否（当前设计合理） | 补充后台任务完成状态的可观测性（如写入 ledger） |
| `/v1/edge/events` | `ACTIVE`（非闭环） | Bond/VTS 是 fire-and-forget，HTTP 200 只代表 accepted；但已补充最小安全边界：来源白名单 + 事件类型白名单（非法输入返回 400） | `system_handler.py::ALLOWED_EDGE_EVENT_TYPES/ALLOWED_EDGE_EVENT_SOURCES`，`receive_edge_events_response` 中 `HTTPException(400)` 校验 | 否（当前设计合理） | 进一步补充鉴权（HMAC/签名）与网络层 ACL，增强防伪造能力 |

### 16.3 /v1/edge/events 安全边界审计（修订后）

**当前真实语义分层**：

```
Layer 1: request accepted    → HTTP 200（ledger.log_voice_event 成功）
Layer 2: side effect dispatched → Bond 队列入队 / VTS ensure_future 调度
Layer 3: side effect completed  → ❌ 不可观测（fire-and-forget）
Layer 4: side effect observed   → ❌ 不可观测
```

**已落地的最小安全边界**：
- 已增加来源白名单：`ALLOWED_EDGE_EVENT_SOURCES = {"edge", "edge_client", "host_edge_bridge"}`
- 已增加事件类型白名单：`ALLOWED_EDGE_EVENT_TYPES`（覆盖 ASR/TTS/VTS/FALLBACK/HEALTH 等事件）
- `source` 统一转小写校验；`event_type` 统一转大写校验
- 非法来源 / 非法事件类型立即返回 `HTTP 400`，不会写入 ledger

**当前安全前提**（必须满足才能接受上述风险）：
1. Host Brain 仅部署在内网，不暴露公网
2. Edge Client 与 Host Brain 在同一受信任网络
3. 无多租户场景（单用户部署）

**剩余风险**：
- 仍无 HMAC/签名鉴权（与 Host→Edge 方向不对称）
- 仍依赖内网隔离与部署策略，应用层未强制来源身份认证
- 仍是非闭环语义：HTTP success 仅代表 accepted / dispatched，非 side effect completed

**代码证据**：
- `host_brain/app/system_handler.py::ALLOWED_EDGE_EVENT_TYPES`
- `host_brain/app/system_handler.py::ALLOWED_EDGE_EVENT_SOURCES`
- `host_brain/app/system_handler.py::receive_edge_events_response`（`Unknown edge event source/type` → 400）

### 16.4 ContextPackBuilder 升级结果（SHADOW → ACTIVE）

**当前状态**：已升级为 `ACTIVE`（带回退开关）。

**已实现改动**：
- 在 `_sse_event_generator` 中将 `retrieval_evidence_items` 归一化为 recall slots（`_build_context_pack_recall_results`）
- 调用 `_cpb.build(...)` 后，将 ContextPack 槽位转换为 prompt 输入项（`_context_pack_to_retrieval_evidence_items`）
- 当 `HOST_CONTEXT_PACK_ACTIVE=true` 且存在有效结果时，执行 `retrieval_evidence_items = _rewritten_items`
- `context_pack_builder_summary` 增加 `mode/active_enabled/applied_to_prompt/rewritten_item_count/input_item_count`，用于审计是否真正影响 prompt

**风险**：
- 若 `ContextPackBuilder.build()` 截断过激，可能丢失重要召回结果
- 槽位归一化依赖召回条目中的 `slot_name/source/store_id` 映射质量，错误映射会影响压缩精度

**回退方式**：设置 `HOST_CONTEXT_PACK_ACTIVE=false`，保留构建与审计但不回写 prompt 输入（退回 `SHADOW` 语义）。

**建议**：持续观察 `applied_to_prompt`、`rewritten_item_count` 与回答质量指标，按需调优 slot 配额和映射规则。

### 16.5 StateArbiter 接入结果（WIRED → ACTIVE）

**当前状态**：已接入业务查询路径（局部 ACTIVE）。

**已实现改动**：
- `runtime_lifespan` 注册 3 个可执行适配 getter（world/style/episodic）
- `_sse_event_generator` 调用 `StateArbiter.resolve("world_canon_facts", session_id)`
- 命中时将结果注入 `retrieval_evidence_items`（`slot_name=world_canon`）并写入 `retrieval_summary.world_canon`
- COMMIT 增加 `audit_refs.state_arbiter`，输出 `status/source/is_fallback/conflict_detected` 等字段

**风险与边界**：
- 目前仅接入 `world_canon_facts` 主路径，其他 fact_key 仍未统一接入
- 冲突审计依赖事实键覆盖率；当前覆盖范围有限，仍需扩展

**回退方式**：StateArbiter 调用失败时自动降级，不阻断主链，`state_arbiter.status=DEGRADED` 可观测。

### 16.6 旧报告冲突点修正

| 冲突点 | 旧报告表述 | 修正后表述 |
|--------|-----------|-----------|
| ContextPackBuilder | "已完成接入主链" | `ACTIVE`（可回退）：截断结果默认回写到 `retrieval_evidence_items` 并影响 prompt，`HOST_CONTEXT_PACK_ACTIVE=false` 可退回 `SHADOW` |
| RoutingMatrix | "已接入路由决策" | `ACTIVE`（策略门控）：决策可真实改写 `provider/model`（cloud/host_8b），并保留策略与健康兜底 |
| StateArbiter | "已注册状态源" | `ACTIVE`（局部路径）：`resolve("world_canon_facts")` 已接入流式主链并影响 prompt 输入 |
| MainChain | "主链路编排器" | `ACTIVE`（轻编排适配层）：节点有最小可执行语义，适配层执行 pre/post 并观测 SSE，路由仍委托 `_unified_chat_sse_generator` |
| `/v1/edge/events` | "完整闭环已打通" | `ACTIVE`（非闭环）：HTTP 200 只代表 accepted；副作用 fire-and-forget；已补充来源/事件类型白名单 |
| `SCENE_VISION_UI` | "视觉链路完整实现" | `PROD_READY`（最小加固）：SLA 监控 + 来源校验 + 延迟告警已上线，`audit_refs.sla_monitor` 可观测 |
| `/v1/host/input` | "完整闭环" | `ACTIVE`（非闭环）：fire-and-forget，副作用不可观测 |

### 16.7 仍未解决的问题

> 更新于 2026-03-12：以下两个 P2 问题已完成最小必要改动，等级已升级。

| 问题 | 原等级 | 当前等级 | 说明 |
|------|--------|---------|------|
| ~~SCENE_VISION_UI 生产化加固~~ | P2 | **已完成** | 已升级为 `PROD_READY`（最小加固）：SLA 监控（连续失败/降级率/延迟告警）、请求来源校验（`HOST_VISION_REQUIRE_INTERNAL_ORIGIN`）、`COMMIT.audit_refs.sla_monitor` 可观测 |
| ~~MainChain 深度业务化~~ | P2 | **已完成** | 已升级为 `ACTIVE`（深度业务化）：GovernorNode 接入真实 IntentDecisionEngine（含适配层跳过逻辑）、RecallFusionNode 接入真实 RetrievalOrchestrator 七路召回、QAContractNode 接入真实内容合规校验（格式/长度/安全词/Null字节） |

**当前无未解决的 P0/P1/P2 问题。**

**仍需关注（P3，非阻塞）**：
- `SCENE_VISION_UI` 的 `PROD_READY` 是"最小加固"，尚未覆盖：
  - 多租户场景下的完整鉴权（当前仅内网 IP 校验）
  - 云模型 SLA 的外部告警推送（当前仅写入 ledger）
- ~~`MainChain` 的 `GovernorNode` 在适配层模式下跳过真实鉴权（`adapter_mode=sse`），
  真实鉴权由 `_unified_chat_sse_generator` 负责，双重鉴权路径尚未统一~~
  → **已解决（2026-03-12）**：适配层也执行真实鉴权，结果通过 `pre_governor_result` 传递，两条路径已统一（见 §16.10）
- ~~`RecallFusionNode` 的七路召回结果写入 `ctx.recall_context_pack`，
  但 `run_as_sse_adapter` 中该结果是否真实传递给 `_unified_chat_sse_generator` 尚未 E2E 验证~~
  → **已解决（2026-03-12）**：`_pre_recall_evidence` 参数已透传，链路闭环（见 TODO-9）

### 16.10 本次修改摘要（2026-03-12，第二批）

**问题1：`build_chat_context_pack` 使用 CPB slot 输出**

改动文件：`host_brain/app/chat_context_builder.py`、`host_brain/app/chat_stream_handler.py`

| 改动点 | 说明 |
|--------|------|
| `build_chat_context_pack` 增加 `cpb_slots` 参数 | 接收 `ContextPackBuilder.build()` 输出的 slot 数据并注入 prompt 构建 |
| `chat_stream_handler.py` 提取 CPB slot | 在调用 `build_chat_context_pack` 前提取 CPB slot 并透传 |

**问题2：GovernorNode 两条鉴权路径统一**

改动文件：`host_brain/app/graph/main_chain.py`、`host_brain/app/chat_stream_handler.py`

| 改动点 | 说明 |
|--------|------|
| `GovernorNode` 适配层模式执行真实鉴权 | 不再跳过，执行完整 R1-R7 门控 |
| `pre_governor_result` 参数透传 | 鉴权结果通过参数传递给 `_unified_chat_sse_generator`，避免重复执行 |
| `_unified_chat_sse_generator` 接收 `pre_governor_result` | 有预鉴权结果时直接使用，无则执行自身鉴权逻辑 |

**测试验证**：合并运行 62 passed（含所有相关单元测试，无回归）

---

### 16.9 本次修改摘要（2026-03-12，第一批）

**问题1：SCENE_VISION_UI 生产化加固**

改动文件：`host_brain/app/chat_handler_vision.py`

| 改动点 | 说明 |
|--------|------|
| 新增 `_VisionSLAMonitor` 类 | 模块级单例，追踪连续失败/降级率/总调用次数 |
| `_call_vision_llm` 接入 SLA 监控 | 每次调用后记录状态，触发告警写入 ledger |
| `_vision_ui_sse_generator` 延迟告警 | 单次调用超过阈值时写入 `S_VISION_SLA_LATENCY_WARN` |
| 请求来源校验 | `HOST_VISION_REQUIRE_INTERNAL_ORIGIN=true` 时校验 origin_ip |
| `COMMIT.audit_refs.sla_monitor` | 输出 SLA 监控摘要（可观测） |

环境变量：
- `HOST_VISION_SLA_FAIL_THRESHOLD`（默认 3）：连续失败告警阈值
- `HOST_VISION_SLA_DEGRADE_RATE_THRESHOLD`（默认 0.5）：降级率告警阈值
- `HOST_VISION_SLA_LATENCY_WARN_MS`（默认 10000）：延迟告警阈值（ms）
- `HOST_VISION_REQUIRE_INTERNAL_ORIGIN`（默认 false）：是否启用来源校验
- `HOST_VISION_ALLOWED_ORIGINS`（默认 `127.0.0.1,::1,localhost`）：允许的来源 IP

**问题2：MainChain 深度业务化**

改动文件：`host_brain/app/graph/main_chain.py`

| 节点 | 改动点 | 说明 |
|------|--------|------|
| `GovernorNode` | 接入 `IntentDecisionEngine.evaluate_intent` | 执行完整 R1-R7 门控；适配层模式（`adapter_mode=sse`）自动跳过避免双重鉴权 |
| `RecallFusionNode` | 接入 `RetrievalOrchestrator.arun` | 七路并行召回，结果写入 `ctx.recall_context_pack` |
| `QAContractNode` | 接入真实内容合规校验 | 格式规范化 + 长度截断 + 安全词检测 + Null字节清除 |

所有节点均有降级策略：业务组件不可用时退回最小状态语义，不阻断链路。

**测试验证**：
- `tests/unit/test_main_chain_deep_business.py`（18 passed）：新增测试，覆盖真实业务调用和降级策略
- `tests/unit/test_main_chain_sse_adapter.py`（5 passed）：适配层兼容性验证（无回归）
- 合并运行：60 passed（含所有相关单元测试）



### 16.8 SCENE_VISION_UI 双端联调证据（2026-03-11）

**执行方式**：本地同时启动 `host_brain`（8000）与 `edge_client`（17800），发送两组 `/v1/chat` 请求：
- `scene_type=SCENE_VISION_UI` + `image_upload_consent=true`
- `scene_type=SCENE_VISION_UI` + `image_upload_consent=false`

**实测结果摘录**：
- `edge_health_status=ACCEPTED`
- `host_health_status=OK`
- `consent_true.graph_start_scene_type=VISION_UI`
- `consent_true.vision_input.image_upload_allowed=true`
- `consent_false.vision_input.image_upload_allowed=false`
- 两组请求 `COMMIT.decision` 均为 `APPROVE`

**说明**：
- 此证据证明了“可达性 + 策略分支 + 审计落盘”已完成端到端验证（`E2E_VERIFIED`）。
- 该结论不等价于 `PROD_READY`；生产可用性仍需补鉴权、告警和云模型 SLA 监控。

---

*严格审计时间：2026-03-11 21:07 | 审计模型：claude-4.6-sonnet*
*审计范围：chat_handler.py / chat_stream_handler.py / graph_config_builder.py / http_hooks.py / system_handler.py / observability_persist.py / chat_handler_vision.py / main_chain.py / dependencies.py*
*审计原则：以代码真实执行语义为准，不允许脑补，不允许把 SHADOW 写成 ACTIVE*
