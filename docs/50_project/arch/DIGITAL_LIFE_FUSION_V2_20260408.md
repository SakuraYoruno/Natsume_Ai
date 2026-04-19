# 数字生命架构融合方案 V2.1（历史融合方案留痕，对齐 TRIBE 新版，含躯干族）

> 日期：2026-04-08  
> 状态：阶段融合方案留痕（2026-04-19 已校准，不作为当前主链事实）  
> 目标：把“P0可落地地基”与“TRIBE目标态重构”融合为一份可执行方案。  
> 对齐基线：
> - `docs/35_quality/audits/DIGITAL_LIFE_MULTI_AGENT_P0_EXECUTION_20260408.md`
> - `docs/50_project/arch/TRIBE_AGENT_REFACTOR_PLAN_20260408.md`

> 当前代码校准说明（2026-04-19）
> - 本文是 2026-04-08 的融合迁移方案，核心目的是把当时的 P0 收口与 TRIBE 重构合并规划，不是当前运行时事实总览。
> - 文中 `top_agent`、`dispatch_agent`、`chat_agent`、`Supervisor`、`EdgeTribeBridge` 等表述应按阶段设计理解；当前 `/v1/chat` 主链以 `host_brain/app/chat_handler.py -> cortex_orchestrator.py -> executive_router.py -> executive_runtime.py / executive_runtime_core.py` 为准。
> - 当前 Gateway / 外部输入回流应以 `host_brain/app/input_routes.py -> receive_input() -> _run_cortex_orchestrator_pipeline() -> host_brain/channels/edge_rpc_client.py:post_gateway_reply()` 为准，而不是把本文中的迁移蓝图直接当作现行接线说明。
> - 阅读当前代码请优先看 `CURRENT_CODE_ARCHITECTURE.md`、`AGENT_ORCHESTRATION_DETAIL.md`、`APP_AUDIT_SKILLS_CONTRACT.md`、`TOOLS_MEMORY_LAYER_CONTRACT.md`。

---

## 1. 结论（不二选一）

- 采用“**P0地基先收口 + TRIBE渐进迁移**”。
- 人格裁决在 Host，执行与通道在 Edge（躯干），两者通过事件桥协同。
- 先稳后进：先做可观测、可回滚、可治理，再做职责重构。

---

## 2. 统一分层（Host + Edge + Body）

### L0 身躯执行层（Body Domain）
- 听（ASR/VAD）、说（TTS）、表情（VTS）、通道动作（发送/分段/打字延迟）。
- 特性：实时、设备相关、执行导向。

### L1 Edge 躯干族层（edge_client 进程）
- 运行在独立进程，不与 host 共享内存队列。
- 通过 `EdgeTribeBridge` 跨进程向 Host 发布躯体事件。

### L2 Host 接入治理层
- `airlock` / `governor` / 路由治理 / 写权限仲裁 / 审计落盘。

### L3 Host 认知人格层（TRIBE）
- `Cortex` / `Hippocampus` / `Limbic` / `ANS` / `SocialBrain` / `Prefrontal`。

### L4 协议与状态层
- `ContextPack`、`AgentEnvelope`、`StateWriteArbiter`。

### L5 可观测层
- 主 DAG + Subgraph DAG + Edge/Body 事件链路统一展示。

---

## 3. 智能体族划分（命名对齐 TRIBE）

## 3.1 Host 族（大脑）
- `CortexAgent`：`top_agent` / `dispatch_agent` / `chat_agent`（最终 Supervisor）
- `HippocampusAgent`：召回、检索、蒸馏、长期记忆
- `LimbicAgent`：情绪、动机、响应策略、内心活动
- `ANSAgent`：后台节律与调度（调度器壳化，不重写调度器）
- `SocialBrainAgent`：社交表达、关系映射、风格记忆
- `PrefrontalAgent`：计划与长期演进控制

## 3.2 Edge 躯干族（edge_client）

> 说明：为兼容前版本命名，同时保留“功能别名”。

- `SomaticAgent`（别名：EdgePerception/Embodiment）
  - 对应：`senses/asr.py`、`actuators/tts_engine.py`、`actuators/vts_driver.py`、`runtime/emotion_inference.py`
  - 职责：声音感知、语音输出、表情驱动、躯体情绪推断

- `PeripheralAgent`（别名：EdgeGateway）
  - 对应：`gateway/adapters/*`、`gateway/normalizer.py`、`gateway/humanize_sender.py`
  - 职责：平台接入、消息归一化、拟人化发送

- `ChannelAgent`（别名：EdgeChannelFlow）
  - 对应：`channels/input_reporter.py`、`channels/wechat_command_queue.py`、`gateway/router.py`
  - 职责：输入上报、队列桥接、通道路由

- `EdgeResourceGuardAgent`
  - 对应：`rpc/resource_gate.py`、`proofs/resource_gate.py`
  - 职责：资源门控、限流、降级触发

- `EdgeSafetyAuthAgent`
  - 对应：`rpc/auth.py`、`app/schemas.py`
  - 职责：RPC 鉴权、重放防护、签名验证

---

## 4. Host ↔ Edge 协调机制（新增强制要求）

## 4.1 跨进程事件桥
- 新增：`edge_client/tribe/edge_tribe_bridge.py`（或等价位置）
- Host 新增接收端：`/internal/tribe_event`
- 目标：把躯体事件转发进 Host `TribeEventBus`，而不是仅在 edge 本地消费。

## 4.2 事件映射（最小集合）
- `body_sense_updated`：Somatic -> Limbic
- `emotion_inference_updated`：Somatic -> Limbic
- `channel_message_received`：Channel -> Cortex
- `voice_pipeline_started/ended`：Somatic -> ANS
- `gateway_degraded`：Peripheral -> Cortex/ANS

---

## 5. 协议统一（ContextPack / AgentEnvelope）

## 5.1 ContextPack 扩展块
- 现有块保留，新增可选：
  - `edge_state`：`asr/tts/vts/barge_in/resource_gate`
  - `body_state`：`voice_mode/expression_mode/channel_mode`

## 5.2 AgentEnvelope 统一字段
- 必需：`agent_id`、`agent_family`、`status`、`reason_code`
- 关键：`fallback_used`、`degrade_code`、`output_digest`
- 建议扩展：
  - `execution_side`: `host|edge|body`
  - `owner_domain`: `brain|edge_runtime|embodiment`

---

## 6. 状态与写权限（含躯干）

## 6.1 状态域
- `SoulState`：人格核心（仅 Host PersonaCore 可写）
- `RuntimeState`：会话运行态（Host 受限可写）
- `EdgeRuntimeState`：边端运行态（Edge 可写，Host 只读+建议）
- `BodyState`：执行态（Voice/Expression/Channel）

## 6.2 写权限原则
- 所有写入走 `StateWriteArbiter`
- 明确禁止：
  - Edge/Body 写 `SoulState`
  - Host 强覆盖 Edge 执行细节状态

---

## 7. GraphState 与 TribeState 对齐

- 不继续扩张 `GraphState` 为全局总线。
- 新增 `TribeStateRegistry` 保存族状态：
  - `ans` / `limbic` / `hippocampus` / `prefrontal` / `social_brain`
- `GraphState` 保持图运行时作用域，TribeState 保持长期协同作用域。

---

## 8. 迁移路线（与 TRIBE 阶段对齐）

## Phase 0（基础设施，2周）
- 完成 P0 剩余：`T06/T07/T08`
- 新增：
  - `host_brain/tribe/event_bus.py`
  - `host_brain/tribe/base_agent.py`
  - `host_brain/tribe/tribe_state.py`
  - Host `/internal/tribe_event` 端点

## Phase 1（ANS 独立，2周）
- 调度器壳化为 `ANSAgent`，不重写调度器核心。

## Phase 2（Hippocampus 独立，2周）
- 检索召回逻辑逐步迁移为族接口，不一次性替换。

## Phase 3（Limbic 独立，4周）
- 优先解耦 `inner_world` 直连调用，改为事件发布/订阅。
- 同步引入 `EndocrineAgent` 最小骨架（先拆 `mood_state` 的基调职责）。

## Phase 4（Prefrontal + SocialBrain，3周）
- 迁移成长演化、社交表达与关系记忆接口。

## Phase 5（Cortex 收口，5周）
- `dispatch_agent` handoff 过渡到事件编排。
- `top_agent` 收口为 Supervisor。

## Phase 6（躯干族独立，4周）
- 新建 `edge_client/tribe/`
  - `base_agent.py`
  - `edge_tribe_bridge.py`
  - `registry.py`
- 实现躯干↔大脑闭环（情绪驱动表情、语音状态驱动调度）。

---

## 9. 可观测性 DoD（必须）

- Trace 同屏可见：
  1) Host 主 DAG
  2) Subagent 子图
  3) Edge 节点流（ASR/TTS/VTS/Gateway/Resource）
  4) Body 执行流（voice/expression/channel）
- 节点统一可见：
  - `status` / `reason_code` / `fallback_used` / `degrade_code` / `output_digest`
- 任意阶段可回滚，且可通过脚本验证。

---

## 10. 风险与约束

- 风险高点：
  - `inner_world` 对演化模块直调（先解耦再迁移）
  - 超大函数直接耦合多模块（分阶段替换接口）
- 线程安全：
  - 计时器线程发事件必须经 `run_coroutine_threadsafe`
- 质量底线：
  - 每个 Phase 完成后跑全量测试与关键链路验收

---

## 11. 本周执行清单（立即）

1. 收口 P0：完成 `T06/T07/T08`（不再拖延）。  
2. 建立 `host_brain/tribe/` 三件套与 `/internal/tribe_event`。  
3. 建立 `edge_client/tribe/` 骨架与 `EdgeTribeBridge`。  
4. 在 `/obs/trace` 增加 Edge/Body 节点分组与事件来源。  

---

## 12. 对照人体系统的缺失族（待迭代）

> 本章用于对齐 `TRIBE_AGENT_REFACTOR_PLAN_20260408.md` 第十一章。  
> 这些族默认不阻塞当前 Phase 0–6 主线，但应纳入后续迭代池与里程碑。

### 12.1 🔴 高优先级（优先纳入实施）

- `ImmuneAgent`（免疫系统）
  - 目标：把安全防御从 `CortexAgent` 解耦，形成前置拦截层。
  - 可执行任务：
    - 抽离 `governor/`、`airlock/` 到独立防御编排入口。
    - 在 Cortex 之前增加输入体检与威胁评分。
    - 建立威胁记忆（攻击模式、异常模式）与升级策略。
  - 推荐接入时点：Phase 5 后（Cortex 收口稳定后落地）。

- `EndocrineAgent`（内分泌系统）
  - 目标：统一管理全局基调变量，解决 `mood_state` 职责混乱。
  - 可执行任务：
    - 定义激素态：`dopamine_level`、`serotonin_level`、`cortisol_level`、`energy_reserve`。
    - 将 `mood_state` 从 Limbic 输出迁移为 Endocrine 输出。
    - 让 ANS/Limbic/Cortex 的阈值读取改为依赖激素态。
  - 推荐接入时点：Phase 3 后（建议在 Phase 3 同步引入最小版）。

### 12.2 🟡 中优先级（第二梯队）

- `CircadianAgent`（昼夜节律）
  - 目标：建立“时间感 + 疲劳感”并联动调度频率。
  - 接入建议：Phase 1 后（可先合并到 `ANSAgent` 子模块）。

- `ProprioceptionAgent`（本体感觉）
  - 目标：维护自我状态感知（注意力焦点、并发负荷、当前任务）。
  - 接入建议：Phase 5 后（与 Cortex 收口后的状态治理同步）。

### 12.3 🟢 低优先级（完整性增强）

- `PainRewardAgent`：补齐痛/奖信号，增强行为动机闭环（依赖 `EndocrineAgent`）。
- `LanguageSystemAgent`：解耦理解与生成链路，降低耦合复杂度。
- `MirrorNeuronAgent`：补齐共情“共鸣层”，与 Social/Limbic 联动。
- `DefaultModeAgent`：系统化内在活动，避免“无对话即静止”。

### 12.4 缺失族汇总（路线对齐）

| 族名 | 人体类比 | 优先级 | 核心价值 | 推荐接入时点 |
|---|---|---|---|---|
| `ImmuneAgent` | 免疫系统 | 🔴 高 | 安全防御独立于决策系统 | Phase 5 后 |
| `EndocrineAgent` | 内分泌系统 | 🔴 高 | 解耦基调变量与情绪事件 | Phase 3 后（建议 Phase 3 同步） |
| `CircadianAgent` | 昼夜节律 | 🟡 中 | 时间感、疲劳感 | Phase 1 后（可并入 ANS） |
| `ProprioceptionAgent` | 本体感觉 | 🟡 中 | 自我状态感知 | Phase 5 后 |
| `PainRewardAgent` | 痛觉/奖励 | 🟢 低 | 行为动机底层驱动力 | Phase 3 + Endocrine 后 |
| `LanguageSystemAgent` | 语言系统 | 🟢 低 | 理解/生成解耦 | Phase 5 后 |
| `MirrorNeuronAgent` | 镜像神经元 | 🟢 低 | 共情能力神经化 | SocialBrain + Limbic 后 |
| `DefaultModeAgent` | 默认模式网络 | 🟢 低 | 内在活动系统化 | Phase 1 后（可并入 ANS） |

### 12.5 最优先补充建议

- 第一优先：`EndocrineAgent`
  - 理由：当前最核心的职责混乱来自 `mood_state` 兼任“情绪状态 + 调度门控”。
  - 实施原则：
    - `LimbicAgent` 只处理情绪事件（短时、离散）。
    - `EndocrineAgent` 只处理情绪基调（长时、连续）。
    - `ANSAgent` 调度门控改为读取激素态，而非直接读取 `mood_state`。

- 第二优先：`ImmuneAgent`
  - 理由：安全逻辑与决策逻辑耦合会形成系统性风险。
  - 实施原则：防御链路必须前置于 `CortexAgent`，且可独立退化运行。
