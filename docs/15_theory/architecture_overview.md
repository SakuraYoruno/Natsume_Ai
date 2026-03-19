受众：MASTER
标题：Natsume AI 整体架构全景文档

# Natsume AI 整体架构全景文档

> 生成时间：2026-03-10 | 最后更新：2026-03-19（代码重构后文档同步）

---

## 一、双端总体架构

```mermaid
graph TB
    subgraph HOST["🖥️ Host Brain（主脑服务）"]
        direction TB
        subgraph ENTRY["入口层"]
            FA[FastAPI App<br/>:8000]
            WC[WeChat Channel]
            VTS_H[VTS Channel]
        end
        subgraph AIRLOCK["气闸层 Airlock"]
            QUA[Quarantine<br/>入站隔离]
            SAN[Sanitizer<br/>出站净化]
            SBX[Sandbox<br/>代码沙箱]
            DSBX[DockerSandbox<br/>容器沙箱]
        end
        subgraph GOV["治理层 Governor（三权分立）"]
            IDE[IntentDecisionEngine<br/>立法/决策]
            RULES[Rules R1-R7<br/>门控规则]
            TOK[TokenService<br/>令牌签发]
            RATE[RateLimitStore<br/>速率限制]
            TIER[TierPolicy<br/>分层治理]
        end
        subgraph ORCH["编排层 Integrations"]
            GE[GraphExecutor<br/>LangGraph]
            GR[GraphRuntime]
            LGW[LlmGateway<br/>模型路由]
            GBR[GraphBranch<br/>并行分支]
            GCK[GraphCheckpoint<br/>断点续跑]
            GEV[GraphEvidence<br/>证据平面]
            GST[GraphStreaming<br/>流式输出]
        end
        subgraph SOUL["灵魂层 Soul"]
            META[Metabolism<br/>情感代谢 VAD]
            QA[QA Contract<br/>本我/超我]
            CTX[ContextPack<br/>上下文打包]
            PB[PromptBuilder<br/>提示词构建]
            EMO[EmotionContext<br/>情感上下文]
        end
        subgraph MEM["记忆层 Memory"]
            EPI[EpisodicStore<br/>情节记忆]
            CTX_C[ContextCompressor<br/>上下文压缩]
            DIST[LongTermDistiller<br/>长期蒸馏]
            CANO[WorldCanonStore<br/>世界观SSOT]
            STYLE[StyleCanonStore<br/>风格Canon]
            CHROMA[ChromaDB<br/>向量数据库]
            ARBITER[StateArbiter<br/>六路状态源仲裁]
            GUARD[MemoryGuard<br/>写入守卫7规则]
        end
        subgraph RETR["检索层 Retrieval（七路召回）"]
            RF[RecallFusion<br/>融合召回]
            EMB[Embedding<br/>向量化]
            FTS[FullTextSearcher<br/>全文检索]
            HF[HybridFusion<br/>混合融合]
            RR[Reranker<br/>重排序]
            EP[EvidencePack<br/>证据包]
            RPB[RecallPackBuilder<br/>token预算组装]
        end
        subgraph BOND["关系层 Bond"]
            BD[BondDynamics<br/>关系动力学]
            BSE[BondStageEngine<br/>阶段晋级]
            NEO[Neo4j Client<br/>图数据库]
        end
        subgraph SCHED["调度层 Scheduler"]
            NS[NatsumeScheduler<br/>定时调度]
            TR[TaskRegistry<br/>任务注册]
        end
        subgraph SKILLS["技能层 Skills"]
            DG[DreamGenerator<br/>梦境生成]
            DW[DiaryWriter<br/>日记写作]
            PT[ProactiveTalk<br/>主动发言]
            FM[FocusMode<br/>专注模式]
            DN[DesktopNote<br/>桌面便签]
            CUA[ClawdUIAction<br/>UI操控]
        end
        subgraph MCP_L["MCP层"]
            TB[ToolBroker<br/>工具代理 mcp/tool_broker.py]
            TREG[ToolRegistry<br/>工具注册]
            MSB[McpSkillBridge<br/>技能桥接]
        end
        subgraph AUDIT["审计层 Audit"]
            LED[SQLiteLedger<br/>审计账本]
            VAULT[ArtifactVault<br/>制品库 lsp/vault.py]
            RET[Retention<br/>数据保留]
        end
        subgraph VIS["视觉层 Vision"]
            CAM[CameraEvent<br/>摄像头事件]
            OCR[ScreenOCR<br/>屏幕识别]
            DET[DetectorRegistry<br/>检测器注册]
        end
        subgraph OBS["可观测层 Observability"]
            OBR[ObsRoutes<br/>监控路由]
            WUI[WebUI<br/>监控面板]
        end
    end

    subgraph EDGE["📱 Edge Client（边缘客户端）"]
        direction TB
        subgraph EDGE_APP["Edge 入口"]
            EFA[Edge FastAPI<br/>:8100]
        end
        subgraph EDGE_SENSE["感知层 Senses"]
            ASR[ASR<br/>语音识别]
        end
        subgraph EDGE_ACT["执行层 Actuators"]
            TTS[TTS Engine<br/>GPT-SoVITS]
            SPE[Speech<br/>播放队列]
            VTS_E[VTS Driver<br/>Live2D驱动]
        end
        subgraph EDGE_RPC["RPC层"]
            AUTH[Auth<br/>Nonce验证]
            ER[EventReporter<br/>事件上报]
            FLM[FallbackLLM<br/>本地兜底]
            RG[ResourceGate<br/>资源门控]
            VC[Voice RPC<br/>语音RPC]
        end
        subgraph EDGE_RT["运行时"]
            ERT[EdgeRuntime<br/>运行时]
            FBM[FallbackModel<br/>本地模型]
        end
    end

    USER((👤 用户)) -->|HTTP/WS| FA
    USER -->|微信| WC
    EDGE_APP -->|RPC| FA
    FA --> AIRLOCK
    AIRLOCK --> GOV
    GOV --> ORCH
    ORCH --> SOUL
    SOUL --> MEM
    MEM --> RETR
    ORCH --> BOND
    ORCH --> SKILLS
    SKILLS --> MCP_L
    ORCH --> AUDIT
    VIS --> ORCH
    SCHED --> SKILLS
    SCHED --> ORCH
    OBS --> LED
    ASR -->|语音流| ER
    ER -->|事件| FA
    FA -->|TTS指令| VC
    VC --> TTS
    TTS --> SPE
    SPE -->|音频| USER
    VTS_E -->|表情/动作| USER
```

---

## 二、Host 端详细模块说明

### 1. 入口层（Entry）

| 文件 | 职责 |
|------|------|
| `app/fastapi_app.py` | FastAPI 主应用，挂载所有路由，管理生命周期 |
| `app/main.py` | 启动入口，初始化所有服务依赖 |
| `channels/wechat.py` | 微信消息接入适配器 |
| `channels/vts.py` | VTS（Live2D）消息通道 |

---

### 2. 气闸层（Airlock）

```
用户输入 → Quarantine（入站隔离）→ 业务处理 → Sanitizer（出站净化）→ 用户输出
                                        ↓
                              Sandbox（代码执行沙箱）
                              DockerSandbox（容器隔离）
```

| 文件 | 职责 |
|------|------|
| `airlock/quarantine.py` | 入站内容隔离检查，拦截危险输入 |
| `airlock/sanitizer.py` | 出站内容净化，防止信息泄露 |
| `airlock/sandbox.py` | 代码执行沙箱（进程隔离） |
| `airlock/docker_sandbox.py` | Docker 容器级沙箱 |

---

### 3. 治理层 Governor（三权分立）

```mermaid
graph LR
    INTENT[Intent 提案] --> IDE
    subgraph 立法权
        RULES[R1-R7 门控规则]
        TIER[TierPolicy 分层治理]
    end
    subgraph 行政权
        IDE[IntentDecisionEngine]
        RATE[RateLimitStore]
    end
    subgraph 司法权
        TOK[TokenService 令牌]
        LED2[SQLiteLedger 审计]
    end
    IDE --> RULES
    IDE --> RATE
    IDE --> TOK
    IDE --> LED2
    TOK -->|CommittedRouteDecision| ORCH2[编排层]
```

**R1-R7 门控规则：**

| 规则 | 说明 |
|------|------|
| R1 | 意图解析合法性 |
| R2 | 序列号防重放 |
| R3 | 过期检测（5分钟窗口） |
| R4 | 速率限制 |
| R5 | 系统模式检查（normal/focus/gaming/maintenance/locked） |
| R6 | 令牌签名验证 |
| R7 | 分层治理策略（M42） |

---

### 4. Agent 编排层（Integrations / LangGraph）

> **P15 重构说明**：所有复杂场景统一走 `PROFILE_WORKFLOW_SUPERVISED` 图，入口为 `supervisor_dispatch → agent_dispatch → judge_agent → chat_render_node`。旧的 `core_reasoner_primary → scene_router_post_core → workbench_*` 长链已废弃。

```mermaid
graph TD
    GCB[GraphConfigBuilder<br/>图配置构建] --> GE
    GE[GraphExecutor] --> |step| NODE
    subgraph NODE["节点执行（PROFILE_WORKFLOW_SUPERVISED）"]
        N1[supervisor_dispatch<br/>规则/LLM路由]
        N2[agent_dispatch_node<br/>动态分派]
        N3[chat_agent_handler<br/>对话Agent]
        N4[code_agent_handler<br/>代码Agent]
        N5[translate_agent_handler<br/>翻译Agent]
        N6[intent_agent_handler<br/>意图Agent]
        N7[retrieval_agent_handler<br/>检索Agent]
        N8[judge_agent_node<br/>裁判节点]
        N9[chat_render_node<br/>最终渲染]
    end
    GE --> GBR[GraphBranch 并行分支]
    GE --> GCK[GraphCheckpoint 断点续跑]
    GE --> GEV[GraphEvidence 证据平面]
    GE --> GST[GraphStreaming 流式输出]
    GE --> LGW[LlmGateway 模型路由]
```

**场景路由（SceneRouter）：**

| 场景 | 触发条件 | execution_profile | 节点序列 |
|------|----------|------|------|
| `SCENE_CHAT` | 普通对话 | `PROFILE_CHAT_FAST` | `chat_direct_ja → chat_render_node` |
| `SCENE_EXEC_INTENT` | 执行意图关键词 | `PROFILE_INTENT_EXEC` | `intent_agent_entry → safety_verifier → judge_agent → chat_render_node` |
| `SCENE_WORK_CODE` | 代码关键词 | `PROFILE_WORKFLOW_SUPERVISED` | `supervisor_dispatch → agent_dispatch → [judge_agent] → chat_render_node` |
| `SCENE_WORK_TRANSLATE` | 翻译关键词 | `PROFILE_WORKFLOW_SUPERVISED` | `supervisor_dispatch → agent_dispatch → [judge_agent] → chat_render_node` |
| `SCENE_VISION_UI` | 截图/界面关键词 | — | 视觉处理节点 |

---

### 5. 模型路由（LlmGateway）

```mermaid
graph LR
    REQ[请求] --> LGW
    LGW -->|主模型| CLOUD[Cloud API<br/>Gemini/OpenAI/DeepSeek]
    LGW -->|备用| HOST8B[Host 8B<br/>Qwen3:8b Ollama]
    LGW -->|本地兜底| EDGE3B[Edge 3B<br/>本地小模型]
    LGW -->|Mock| MOCK[Mock Provider<br/>测试]
    CLOUD -->|失败| HOST8B
    HOST8B -->|失败| EDGE3B
```

**路由决策：**

| 路由 | 说明 |
|------|------|
| `host_8b` | 本地 Ollama（`http://127.0.0.1:11434/v1`，Qwen3:8b） |
| `cloud` | 云端 API（Gemini Primary → Gemini Retry → OpenAI → DeepSeek） |
| `edge_3b` | Edge 端本地小模型 |
| `skills` | 技能专用路由 |

> `adjust_gen_params_for_emotion`：根据 VAD 情绪向量动态调整 `temperature`、`top_p` 等生成参数。

---

### 6. 上下文压缩（ContextCompressor）

| 策略 | 说明 |
|------|------|
| `sliding_window` | 保留最近 N 轮，直接截断 |
| `summary` | 调用 LLM 生成历史摘要，不可用时降级 |
| `hybrid` | 保留最近 6 轮 + LLM 摘要历史轮次 |

压缩前自动写入 `COMPRESS_BEFORE_SAVE` 审计事件，确保可追溯。

---

### 7. 七路召回（RecallFusion）

```mermaid
graph TD
    Q[用户查询] --> EMB[Embedding 向量化]
    EMB --> R1[Route 1: sqlite_ledger<br/>SQLite 审计日志]
    EMB --> R2[Route 2: neo4j_bond<br/>Neo4j 关系图]
    EMB --> R3[Route 3: episodic_vector<br/>ChromaDB 情节向量]
    EMB --> R4[Route 4: style_canon<br/>风格 Canon 向量]
    EMB --> R5[Route 5: world_canon<br/>世界观 Canon 向量]
    EMB --> R6[Route 6: artifact_vault<br/>制品库向量]
    EMB --> R7[Route 7: persona_utterance<br/>人格话语向量]
    R1 & R2 & R3 & R4 & R5 & R6 & R7 --> HF[HybridFusion<br/>混合融合]
    HF --> RR[Reranker<br/>Qwen3-Reranker-0.6B]
    RR --> EP[EvidencePack<br/>证据包]
    EP --> RPB[RecallPackBuilder<br/>token预算截断+冲突检测<br/>总上限2700 tokens]
    RPB --> ARB[StateArbiter<br/>六路状态源仲裁]
    ARB --> CTX[ContextPack 注入]
```

**混合检索权重：** 向量检索 70% + 全文检索（FTS5）30%

**时效加分机制：**

| 时间窗口 | 加分 |
|----------|------|
| 同 Session 命中 | +0.25 |
| 5 分钟内 | +0.20 |
| 1 小时内 | +0.10 |
| 1 天内 | +0.05 |

---

## 三、数字生命灵魂人格实现

### 1. 情感层（VAD 三维模型）

```mermaid
graph LR
    subgraph VAD["EmotionVector（M28-A）"]
        V[Valence 效价<br/>-1.0 ~ 1.0]
        A[Arousal 唤醒度<br/>-1.0 ~ 1.0]
        D[Dominance 支配感<br/>-1.0 ~ 1.0]
    end
    VAD -->|decay| DECAY[情绪衰减<br/>向中性靠近]
    VAD -->|blend| BLEND[情绪混合<br/>weight=0.3]
    VAD -->|to_mood_state| MOOD[离散情绪<br/>calm/steady/engaged/tired/critical]
    VAD -->|to_emotion_tag| TAG[情绪标签<br/>NEUTRAL/HAPPY/SAD/ANGRY<br/>SURPRISED/THINKING/CALM/EXCITED]
    TAG -->|emotion_requirement| MCP_FILTER[MCP工具情感过滤]
    TAG -->|adjust_gen_params| LLM_PARAM[LLM生成参数调节]
```

**情感标注在记忆中的体现（EpisodicRecord）：**

| 字段 | 说明 |
|------|------|
| `emotion_tag` | 离散情绪标签 |
| `emotion_valence` | 效价值 |
| `emotion_arousal` | 唤醒度值 |

---

### 2. 本我 / 自我 / 超我（QA Contract）

```mermaid
graph TD
    LLM_OUT[LLM 输出] --> QA[QA Contract<br/>enforce_qa_contract]
    subgraph 超我["超我（Superego）— 道德约束"]
        FEC[FALSE_EXECUTION_CLAIM<br/>禁止虚假执行声明]
        CAF[CANON_AS_FACT<br/>禁止将设定当事实]
        WCANON[WCANON_SLOT<br/>世界观槽位保护]
    end
    subgraph 自我["自我（Ego）— 格式规范"]
        FMT[JSON格式验证]
        REQ[必填字段检查<br/>narrative/intent_draft/meta]
        EMO_V[emotion_tag 合法性]
    end
    subgraph 本我["本我（Id）— 原始驱动"]
        NAR[narrative 叙事生成]
        INT[intent_draft 意图草案]
        EMO_D[情感驱动输出]
    end
    QA --> 超我
    QA --> 自我
    QA --> 本我
    超我 -->|违规| BLOCK[拦截/重写]
    自我 -->|格式漂移| REWRITE[自动修复]
    本我 -->|通过| OUTPUT[最终输出]
```

---

### 3. 梦境系统（DreamGenerator）

```
触发条件：sys_mode == "sleep" 或 idle == True
    ↓
读取当日 EpisodicStore（top_k=20）
    ↓
LLM 生成梦境叙事（task="dream_generation"）
    ↓
写入 EpisodicStore（category="dream", evidence_level="NARRATIVE_ONLY"）
```

- 无素材时跳过（`SCHED_NO_CONTENT`）
- LLM 不可用时降级返回 `STATUS_DEGRADED`
- 梦境内容带 SHA-256 哈希存档

---

### 4. 遗忘系统（LongTermDistiller）

```
定时触发（NatsumeScheduler）
    ↓
从 EpisodicStore 召回 top-k 记忆
    ↓
MemoryDistillationEngine 生成语义记忆（6大主题）
    ↓
生成 World Canon pending 提案（最多5个/周期）
    ↓
等待人工/自动审核 → 写入 WorldCanonStore SSOT
```

**6大蒸馏主题：**

1. 用户日常习惯与偏好
2. 重要事件与里程碑
3. 情感状态与心理变化
4. 用户目标与计划
5. 关系与社交互动
6. 情感关系史（M28-J）

> **幂等保护：** 相同 `source_hash` 不重复生成提案

---

### 5. 日记系统（DiaryWriter）

```
每日定时触发
    ↓
读取当日 AuditLog（对话记录）+ EpisodicStore
    ↓
无实质对话 → 跳过（不伪造）
    ↓
LLM 生成日记叙事（task="diary_generation"）
    ↓
双写：① 本地文件（diary/YYYY-MM-DD.md）
      ② EpisodicStore（category="diary"）
    ↓
提取 drift_suggestion → 提交 Canon Evolution 提案（慢立法）
```

---

### 6. 慢立法（World Canon Evolution）

```mermaid
graph LR
    DIARY[日记系统] -->|drift_suggestion| PROP[Canon Proposal<br/>status=pending]
    DIST[长期蒸馏] -->|语义记忆| PROP
    CANON_R[canon_routes.py] -->|人工审核| PROP
    PROP -->|审核通过| SSOT[WorldCanonStore SSOT<br/>canon/world/*.json]
    SSOT -->|自动同步| VEC[Canon Vector<br/>ChromaDB]
    SSOT -->|版本控制| HIST[历史版本]
```

**World Canon 文档：**

| 文件 | 内容 |
|------|------|
| `wc-001-system-terms.json` | 系统术语 |
| `wc-002-architecture-invariants.json` | 架构不变量 |
| `wc-003-persona-core.json` | 人格核心 |

> **防护机制：** 禁止写入"已执行/执行完成/已保存"等完成性表述（`WORLD_EXECUTION_FACT_TOKENS`）

---

### 7. Bond 关系系统

```mermaid
graph LR
    subgraph 阶段["关系阶段（6级）"]
        S1[STRANGER] --> S2[ACQUAINTANCE]
        S2 --> S3[FRIEND]
        S3 --> S4[CLOSE_FRIEND]
        S4 --> S5[CONFIDANT]
        S5 --> S6[SOULMATE]
    end
    subgraph 积分["互动积分"]
        I1[INTERACTION_MINIMAL +1]
        I2[INTERACTION_ENGAGED +2]
        I3[INTERACTION_DEEP +5]
        I4[EMOTIONAL_SUPPORT +8]
        I5[MILESTONE +10]
        I6[IDLE_DECAY -2]
    end
    积分 --> BSE[BondStageEngine<br/>自动晋级/降级]
    BSE --> NEO[Neo4j 图数据库]
    NEO --> RETR_B[七路召回 Route 2]
```

**晋级阈值：**

| 阶段 | 所需积分 |
|------|----------|
| STRANGER | 0 |
| ACQUAINTANCE | 10 |
| FRIEND | 30 |
| CLOSE_FRIEND | 70 |
| CONFIDANT | 150 |
| SOULMATE | 300 |

---

## 四、MCP 与 Skill 整合

```mermaid
graph TD
    subgraph MCP["MCP 层"]
        TB[ToolBroker<br/>工具代理]
        TREG[ToolRegistry<br/>SQLite 注册表]
        MSB[McpSkillBridge<br/>技能桥接]
    end
    subgraph SKILL["Skill 层"]
        HL[HotLoader<br/>热加载]
        BR[BuiltinRegistry<br/>内置注册]
        IR[IdeRegistry<br/>IDE注册]
        LDR[Loader<br/>加载器]
    end
    subgraph IMPL["Skill 实现"]
        DG2[dream_generator]
        DW2[diary_writer]
        PT2[proactive_talk]
        FM2[focus_mode]
        DN2[desktop_note]
        CUA2[clawd_ui_action]
    end
    TB --> TREG
    TB --> MSB
    MSB --> SKILL
    SKILL --> IMPL
    HL -->|文件监听| IMPL
    TREG -->|emotion_requirement| EMO_FILTER[情感适用性过滤]
```

**ToolMeta 字段：**

| 字段 | 说明 |
|------|------|
| `access_level` | PUBLIC / INTERNAL / RESTRICTED |
| `risk_level` | LOW / MEDIUM / HIGH |
| `reversible` | 是否可撤销 |
| `emotion_requirement` | 情感适用性（M28-N） |

---

## 五、ClawdBot 实现

```mermaid
graph TD
    VIS2[Vision 视觉层] -->|截图/OCR| CAM2[CameraEvent]
    CAM2 -->|element_found/confidence| CUA3[ClawdUIAction]
    CUA3 -->|redline_detected| BLOCK2[拦截 VISUAL_REDLINE]
    CUA3 -->|confidence < 0.80| BLOCK3[拦截 UI_CONFIDENCE_LOW]
    CUA3 -->|element_not_found| BLOCK4[拦截 UI_ELEMENT_NOT_FOUND]
    CUA3 -->|通过| VAULT2[ArtifactVault<br/>before/after 截图存档]
    VAULT2 --> LED3[SQLiteLedger<br/>clawd_ui_action 审计]
    LED3 --> EXEC[执行 UI 动作]
```

**安全门控：**

1. `redline_detected`：红线场景直接拦截
2. `element_found`：目标元素必须存在
3. `confidence ≥ 0.80`：置信度门槛
4. 执行前后截图双存 ArtifactVault，全程可审计

---

## 六、Edge 端详细架构

```mermaid
graph TB
    subgraph EDGE2["Edge Client"]
        EFA2[Edge FastAPI :8100]
        subgraph SENSE["感知层"]
            ASR2[ASR 语音识别<br/>Whisper/本地]
        end
        subgraph ACT["执行层"]
            TTS2[TTS Engine<br/>GPT-SoVITS GPU]
            SPQ[SpeechPlaybackQueue<br/>播放队列]
            VTD[VTS Driver<br/>Live2D表情/动作]
            VEM[VTS Expression Map<br/>表情映射]
        end
        subgraph RPC2["RPC 层"]
            AUTH2[Auth Nonce验证]
            ER2[EventReporter<br/>事件上报→Host]
            FLM2[FallbackLLM<br/>本地兜底推理]
            RG2[ResourceGate<br/>GPU/内存门控]
            VC2[Voice RPC<br/>语音指令接收]
        end
        ERT2[EdgeRuntime<br/>运行时管理]
        FBM2[FallbackModel<br/>本地小模型]
    end
    HOST_FA[Host FastAPI] -->|RPC| EFA2
    EFA2 --> AUTH2
    AUTH2 --> VC2
    VC2 --> TTS2
    TTS2 --> SPQ
    SPQ -->|音频流| SPEAKER[🔊 扬声器]
    ASR2 -->|文字| ER2
    ER2 -->|HTTP| HOST_FA
    VTD --> VEM
    VEM -->|WebSocket| VTUBE[VTube Studio]
    RG2 -->|GPU OOM保护| TTS2
    FLM2 -->|Host不可达| LOCAL[本地推理]
```

**TTS 引擎状态机：**

```
UNLOADED → LOADING → READY → SYNTHESIZING → ERROR/SHUTDOWN
```

**Edge 端服务列表：**

| 服务 | 端口/协议 | 说明 |
|------|-----------|------|
| Edge FastAPI | :8100 HTTP | RPC 网关 |
| ASR | 本地进程 | Whisper 语音识别 |
| TTS Engine | GPU 进程 | GPT-SoVITS 语音合成 |
| VTS Driver | WebSocket | Live2D 表情驱动 |
| EventReporter | HTTP→Host | 事件上报 |
| FallbackLLM | 本地推理 | Host 不可达时兜底 |

---

## 七、调度系统（NatsumeScheduler）

```mermaid
graph LR
    NS2[NatsumeScheduler] --> TR2[TaskRegistry]
    TR2 --> T1[dream_generator<br/>sleep/idle触发]
    TR2 --> T2[diary_writer<br/>每日定时]
    TR2 --> T3[proactive_talk<br/>interval触发]
    TR2 --> T4[focus_mode<br/>条件触发]
    TR2 --> T5[health_check<br/>心跳检测]
    TR2 --> T6[edge_probe<br/>Edge探针]
    NS2 -->|Intent| GOV2[Governor 治理链]
    GOV2 -->|Token| SKILL2[Skill 执行]
    SKILL2 -->|Audit| LED4[SQLiteLedger]
```

**触发模式：** `cron` / `interval` / `once`

> 调度器生成的 Intent 与用户请求享有相同治理权重（`proposer_node_id = "scheduler"`）

---

## 八、审计链（SQLiteLedger）

**20+ 张审计表（Migration 0001-0040）：**

| 表 | 内容 |
|----|------|
| `triptych` | 三联审计（请求/响应/错误） |
| `canon_schema` | Canon 变更记录 |
| `memory_ops` | 记忆操作日志 |
| `vault_schema` | 制品库操作 |
| `airlock_schema` | 气闸拦截记录 |
| `channel_io_schema` | 通道 I/O |
| `clawd_ui_schema` | ClawdBot UI 操作 |
| `voice_event_schema` | 语音事件 |
| `retrieval_event_schema` | 检索事件 |
| `vision_event_schema` | 视觉事件 |
| `bond_update_schema` | 关系更新 |
| `scheduler_event_schema` | 调度事件 |
| `world_canon_doc` | 世界观文档 |
| `canon_proposal_log` | Canon 提案日志 |
| `llm_call_log` | LLM 调用日志 |
| `llm_route_log` | 模型路由日志 |
| `subgraph_trace` | 子图追踪 |
| `episodic_fts5` | 情节记忆全文索引 |

---

## 九、整体数据流

```mermaid
sequenceDiagram
    participant U as 用户
    participant FA as FastAPI
    participant AIR as Airlock
    participant GOV as Governor
    participant ORCH as GraphExecutor
    participant LGW as LlmGateway
    participant MEM as Memory/Retrieval
    participant AUD as SQLiteLedger

    U->>FA: 发送消息
    FA->>AIR: Quarantine 入站检查
    AIR->>GOV: Intent 提案
    GOV->>GOV: R1-R7 门控评估
    GOV->>AUD: 写入 triptych
    GOV->>ORCH: CommittedRouteDecision + Token
    ORCH->>MEM: 七路召回 + 上下文压缩
    MEM-->>ORCH: EvidencePack
    ORCH->>LGW: 调用 LLM（主→备→兜底）
    LGW->>LGW: QA Contract 验证
    LGW-->>ORCH: 验证后输出
    ORCH->>MEM: 写入 EpisodicStore
    ORCH->>AUD: 写入审计链
    ORCH->>AIR: Sanitizer 出站净化
    AIR-->>FA: 净化后响应
    FA-->>U: 流式输出
    Note over MEM: 异步：情感代谢/Bond更新/Canon同步
```

---

## 十、技术栈总览

| 层次 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| Agent 编排 | LangGraph + LangChain |
| 向量数据库 | ChromaDB |
| 图数据库 | Neo4j |
| 关系数据库 | SQLite（审计账本） |
| 嵌入模型 | Qwen3-Embedding-0.6B |
| 重排序模型 | Qwen3-Reranker-0.6B |
| TTS | GPT-SoVITS（GPU） |
| ASR | Whisper（本地） |
| Live2D | VTube Studio（WebSocket） |
| 本地 LLM | Ollama（Qwen3:8b） |
| 云端 LLM | Gemini / OpenAI / DeepSeek |
| 容器沙箱 | Docker |
