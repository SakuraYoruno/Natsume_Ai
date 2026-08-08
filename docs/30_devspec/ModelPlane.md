受众：CODEX
标题：Natsume AI Model Plane 当前规格

# Model Plane 当前规格

## 1. 目标

Model Plane 为不同认知角色选择真实模型，同时保证普通陪伴低延迟、敏感上下文最小出网和故障可降级。模型负责理解与自然表达，不拥有事实、关系、执行或人格 activation authority。

## 2. 角色

| 角色 | 典型用途 | 默认要求 |
|---|---|---|
| `surface_reply` | 普通聊天和情感表达 | 低首字延迟、流式、人格一致 |
| `deep_affective` | 混合情绪、冲突、修复和边界 | 最多两个候选、一次重试 |
| `task_planning` | 有副作用任务的计划草案 | 输出可编译结构，不直接执行 |
| `subagent` | 受管 Delegate | 显式目标策略、最小上下文、Node Budget 和 grant；当前仅 Translate 可持有单一模型端口 |
| `research` | 研究与引用 | 来源可核验、usage 完整 |

路由按角色和 capability 选择 provider/model，不再把固定厂商或旧 A/B/C/D 档位写成稳定架构合同。

## 3. 普通陪伴热路径

```text
AffectiveTurnPlan
→ CompanionUtteranceFrame（内存语义骨架）
→ 一次 surface 模型流式请求
→ 中文 display text + 可选日语 speech text
→ 稳定分句质量检查
→ 文本／TTS／Avatar 交付
```

当 `HOST_COMPANION_TTS_LANGUAGE=ja` 时，同一次模型响应携带中文屏显与自然日语 speech text，避免第二次翻译调用。模型不能自行声明七海的 self affect，必须遵守冻结计划。

`CompanionUtteranceFrameV1` 不是新的审计或规划层。它只包含有限枚举：语义焦点、七海姿态、回应动作、事实推断边界、关系姿态、问句策略和收尾方式；不包含用户原文、私密记忆、生成正文或 raw thought，也不单独持久化。普通回合仍为一次请求；高歧义回合仍为同一次请求返回两个候选，最多沿用既有的一次语义重试。

修复与喜忧深路径可以附带一个版本化正向样本片段：当前只包含 5 条经源行号绑定的七海原句，分别提供具体道歉、责任、克制受伤以及喜忧并存的口语节奏。它们常驻代码内存，不调用 Chroma、SQLite 或外部检索，不建立新状态，也不改变一次请求／最多一次重试的预算。其他场景不会收到这组样本。

## 4. 深路径

只有低置信冲突、关系裂痕、修复、边界或 distress 才进入深路径。深路径必须有硬超时；超时后返回明确表达不确定性的安全回复，不能让普通聊天等待数分钟。

## 5. Provider 规则

- provider、model、endpoint 和 credential 由运行配置注入，不硬编码到业务模块。
- 真实验收拒绝 mock、localhost 占位、缓存结果和缺失 usage 的伪回执。
- 记录 provider/model、usage、成本、provider latency、总耗时和 transport identity。
- 模型失败按角色策略降级；规则 fallback 不能伪装成真实模型成功。
- 选择备用 provider 时保留用户可见语义，不重复外发已经展示的分句。

受管翻译不再依赖旧 Graph Executor 或全局 SubAgent Dispatcher。`translate_agent` 的唯一模型调用使用 `graph.workbench.translate.json` 的 server-owned semantic，预算 guard 由冻结的 TaskNode remaining projection 派生，provider/model/credential 仍由 Model Plane 决定。模型只返回翻译 JSON 工件；工具调用、动态 reroute 和直接 Gateway delivery 均不可用。

## 6. 隐私

出站只包含完成当前角色所需的最小上下文。密钥、原始音视频、完整审计链、无关私密记忆和 raw thought 禁止上行。必要的记忆以摘要和来源引用提供。

## 7. 运行指标

普通对话重点测量首可见 Token、总响应时间、fallback 率和中断取消；TTS 另测首段延迟。最终门槛见 PRD。真实 Provider 的性能尚未通过最终验收，因此当前状态保持 `PARTIAL`。

## 8. 代码锚点

- `host_brain/model_runtime/`
- `host_brain/integrations/llm_gateway.py`
- `host_brain/integrations/llm_helpers.py`
- `configs/model_plane/node_registry.yaml`
- `host_brain/surface_runtime/`

若本文件与生产代码冲突，以 [当前代码架构](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md) 和机器状态为准，并在同一提交修正文档。
