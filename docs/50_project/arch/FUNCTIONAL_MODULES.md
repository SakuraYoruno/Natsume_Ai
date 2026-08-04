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
| 表达交付 | `affective_expression_delivery.py`、Surface streaming | `WIRED / PARTIAL` |

## 3. 对话与记忆

| 能力 | 主要实现 | 边界 |
|---|---|---|
| Surface 对话 | `host_brain/surface_runtime/` | 普通闲聊轻量执行 |
| 模型路由 | `host_brain/model_runtime/` | 真实 provider、预算与降级 |
| Life context | `surface_runtime/life_context.py` | 只读 canonical Life projection |
| Memory grounding | `surface_runtime/memory_context.py`、`memory_grounding.py` | 只取必要摘要，不写 self affect |
| 长期记忆 | `host_brain/memory/` | person 与 workspace namespace 分离 |

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
