受众：CODEX
标题：Natsume AI 功能模块

# 功能模块

## 1. Tribe 智能体群

生产 Host 在一个 app-local `TribeRuntimeScope` 中持有 23 个器官式 Agent。它们通过有界事件总线、共享运行投影和 handoff 协作，应用依赖由 `TribeApplicationPorts` 显式注入。

| 子系统 | Agent 家族 | 主要能力 | 状态与边界 |
|---|---|---|---|
| 感知与路由 | Cortex、Thalamus | 上下文汇集、意图编译、路由、handoff | `WIRED` |
| 情感与社会 | Limbic、Mirror Neuron、Social Brain、Pain/Reward | 情感贡献、共情、关系语境、奖惩 | `WIRED / CONTRIBUTOR_ONLY` |
| 执行与自主 | Prefrontal、Initiative Governor、Default Mode | 反思、动机、准备度、内在活动 | `WIRED / PARTIAL` |
| 记忆与语言 | Hippocampus、Language System | 情景记忆、召回融合、语言风格 | `WIRED` |
| 身体与稳态 | ANS、Endocrine、Somatic、Interoception、Circadian、Proprioception | 活动模式、身体信号、昼夜和负荷 | `WIRED / PROJECTION` |
| 行动与协调 | Motor Cortex、Cerebellum、Vestibular、Peripheral | 行动计划、误差修正、稳定和设备边界 | `WIRED / PARTIAL` |
| 防御与恢复 | Immune、Glymphatic | 输入防护、风险、恢复和记忆整理 | `WIRED / PARTIAL` |

`HostTribeRegistry` 提供家族与能力目录，`TribeAgentCatalog` 在 Host scope 内构造唯一 Agent 实例，`InMemoryTribeEventBus` 负责进程内协作。事件总线和 `TribeStateRegistry` 不是长期生命 authority；涉及 self affect、关系和成长的结果必须转为 canonical Life command 后才能持久化。

## 2. 情感陪伴核心

| 能力 | 主要实现 | 状态 |
|---|---|---|
| 用户情绪观察 | `host_brain/digital_life/text_affect_observer.py`、`affect_fusion.py`、Edge emotion inference | `WIRED` |
| Event Appraisal | `host_brain/digital_life/affective_turn_planner.py` | `WIRED / PARTIAL` |
| Self Affect reducer | `host_brain/digital_life/domain_reducer.py`、`reducer.py` | `WIRED` |
| Life journal／projection | `service.py`、`repository_port.py`、`projection.py` | `WIRED` |
| 关系 episode／修复 | `relationship_lifecycle.py`、`relationship_guidance.py` | `WIRED / PARTIAL` |
| 主动陪伴 | `companion_proactive.py`、permission ingress | `WIRED / PARTIAL` |
| 偏好成长 | `companion_profile_lifecycle.py`、feedback ingress | `WIRED / PARTIAL` |
| 表达计划 | `affective_turn_planner.py`、`companion_voice.py` | `WIRED` |
| 语义说话骨架 | `CompanionUtteranceFrameV1`、`surface_utterance_frame.py`、Surface candidate gate | `COMPOSITE_REPAIR_PASS_SCOPE_BOUND / BROADER_HUMAN_REGISTER_PENDING` |
| 表达交付 | `affective_expression_delivery.py`、Surface streaming | `WIRED / PARTIAL` |

## 3. 对话与记忆

| 能力 | 主要实现 | 边界 |
|---|---|---|
| Surface 对话 | `host_brain/surface_runtime/` | 普通闲聊轻量执行 |
| 模型路由 | `host_brain/model_runtime/` | 真实 provider、预算与降级；实时陪伴的 typed thinking hint 只在 DeepSeek adapter 边界映射 |
| Life context | `surface_runtime/life_context.py` | 只读 canonical Life projection |
| Memory grounding | `surface_runtime/memory_context.py`、`memory_grounding.py` | 只取必要摘要，不写 self affect |
| 长期记忆 | `host_brain/memory/` | person 与 workspace namespace 分离 |

语义说话骨架是纯内存编译／渲染模块，不是审计服务。它不访问 repository、不调用模型、不保存用户原文，也不增加普通聊天的同步写；其目的只是让 compact、rich、native stream 和兼容 prompt 不再各自拼接不同的陪伴规则。

## 4. 受管执行

`host_brain/task_runtime/` 负责 TaskGraph 调度、Attempt、Lease、Budget、Review、Verification、Acceptance、Effect、Outbox、Receipt、取消和恢复。`host_brain/agent_runtime/` 负责受管 Delegate／Skill 工作单和 Workspace 接线。

`host_brain/app/governed_delegate_dispatch.py` 是当前 Delegate 目标注册边界：Pentest 为无端口只读 handoff；Translate 为单次 canonical model 调用。翻译只消费冻结 Node Budget，不持有工具、Provider 配置、嵌套规划或交付权限。未登记 Delegate 在持久建图前拒绝。

该模块不应进入普通无副作用聊天的同步热路径。

## 5. 身体与外部通道

| 能力 | 主要实现 | 当前状态 |
|---|---|---|
| Edge RPC | `edge_client/rpc/` | 目标 Edge 真实运行并完成 session／lease／fence／receipt 查询与重连验证 |
| ASR／voice pipeline | `edge_client/runtime/voice_pipeline.py` | 目标设备已暴露低置信噪声洪泛，当前 `FAIL／REMEDIATION_REQUIRED` |
| GPT-SoVITS TTS | Edge TTS adapter + V4 voice bank | 目标 Edge `UP`；物理播放完成后回写 terminal receipt |
| Avatar | `edge_client/actuators/`、gateway embodiment | VTS 已认证，口型／身体动作可用；兼容情绪热键缺失，`DEGRADED` |
| Unified Gateway | `edge_client/gateway/` | 真实平台入站、Host roundtrip、durable outbox、送达回执和重复抑制已通过 |

## 6. 观测与操作

- `host_brain/app/obs_*`：OBS API、指标与读模型。
- `host_brain/app/monitor_ui/`：Vite 前端。
- Nginx Monitor Proxy：托管构建产物并代理 Host/OBS。
- Gateway 页面将“进程／durable transport 可用”和“各平台通道状态已知”分开；平台详情全为 `UNKNOWN` 时显示未确认告警，不再显示空流量健康态。
- Edge 组件按真实子能力投影：VTS、barge-in、播放设备和 resource gate 互不冒充。
- Operator 命令：只提交合同化命令，成功以服务端 receipt 为准。

## 7. 配置、合同与迁移

- `natsume_contracts/`：版本化跨进程数据合同。
- `configs/`：非密钥默认配置、声线和机器状态。
- `host_brain/migrations/` 与 Edge/Gateway migrations：append-only schema 演进。
- `scripts/runtime/`：生产形态的本地启停脚本。

## 8. 模块完成规则

“类存在”或“测试夹具通过”不等于模块完成。模块状态至少需要生产调用点、真实输入、持久恢复、失败边界、目标环境证据和对应文档一致。

## 9. 当前闭环模块边界

| 模块 | 当前 authority | 对外含义 |
|---|---|---|
| Self Affect | `digital_life_runtime_snapshot.hard_state` | 七海当前自身情绪；不是用户情绪镜像 |
| Life Body Source | `observability_runtime/life_body_source.py` | 派生安全枚举给 OBS／Monitor，不拥有情绪写权限 |
| Reply Acceptance | `surface.reply_delivery` acceptance effect | 形成唯一可恢复的 Gateway 回复意图 |
| Gateway Loop Projection | `INPUT_COMPLETED` 中的 Outbox commit marker | 证明 Host 已提交回复 Outbox，不证明平台物理送达 |
| External Delivery | Gateway terminal／平台 ACK | 在存在真实平台证据时证明传输或送达终态 |

Monitor 的 Closed Loop、Reply Outbox、Terminal 三组指标分别展示上述边界，禁止继续把 legacy ACK touch 当作当前主链的必要生产事件。

## 10. 结构收口后的模块责任

| 责任 | 生产模块 | 不再承担 |
|---|---|---|
| 情感流式生成 | `surface_affective_native_stream.py` | Life reducer、设备发送 |
| 情感确定性／延期交付 | `surface_affective_delivery.py` | 模型候选生成 |
| 自我情感折叠 | `affective_domain_reducer.py` | 关系 profile 和主动频率 |
| 关系与主动后效应折叠 | `relationship_domain_reducer.py` | 用户观察融合 |
| Operator Acceptance effect | `operator_acceptance_effects.py` | 通用 Chat／Skill effect 编译 |
| Host 设备命令前门 | `embodiment_commands.py` | Edge 驱动和物理 I/O |
| TTS 情绪韵律 | `edge_client/actuators/tts_prosody.py` | 模型生命周期与播放设备 |

兼容入口仍从原模块重导出必要符号，以保持调用合同；新实现边界本身才是职责 owner。后续修改应直接进入对应模块，禁止把逻辑重新堆回 facade。

Monitor 的生产 E2E 现在可以按验收范围拆开执行：默认仍包含全部页面；显式设置 `MONITOR_UI_INCLUDE_SECURITY_ACCEPTANCE=0` 时，只验证 Host／OBS 健康、Task Runtime、Trace、Observability、Gateway 与真实 read model，不请求 Security Immune 投影。该模式用于本次用户要求的非安全验收，不能把未执行的安全页面写成 PASS。

非安全 Monitor read-model 当前由 45 项 Node 测试覆盖 System Config、Organ System、HTTP Client、Token Metrics、Observability handoff 与 Runtime Health，line／branch／function 为 93.67%／70.83%／83.90%。上述数字的分母明确不含 Security 页面和安全 organ model；完整 npm 默认测试入口仍保留安全模块，未运行项继续记为 `NOT_RUN_USER_SKIPPED`。

Tribe／Prefrontal 的兼容事件处理不再拥有自我情感 authority。即使 app-local `TribeApplicationPorts` 缺失或类型错误，Prefrontal 也必须按 canonical affective life 启用处理，禁止重新调用 Endocrine、Limbic、PainReward 或 volatile Bond writer；这些模块若保留，只能作为只读兼容投影。

喜忧并存的可见文本由 `surface_affective_native_stream.py` 生成、`surface_candidate_guards.py` 验证、`candidate_scoring.py` 排序。只有 canonical typed frame 明确要求 `SHARED_MIXED_RESONANCE / RESONATE_WITH_BOTH_SIDES` 时，候选才必须表达七海自身的高兴与悬心；用户情绪观察本身不能触发该规则，也不能写成七海的自我情绪。该链路复用既有双候选和最多一次质量重试，不拥有新的 journal 或 writer。

实验性的 `mixed_resonance_language.py` 因真实 cohort 未改善而已删除，当前模块图中不存在该生产环节。mixed 仍由 native stream 的同一次模型请求生成、typed Gate 验证和原选择器排序；后续语言微调不得偷偷增加第二次模型或新的情感 writer。
