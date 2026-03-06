受众：CODEX
标题：实现覆盖复核与重点能力详解报告（含链路可视化缺口与落地方案）

# 实现覆盖复核与重点能力详解报告

## 1. 总结结论
- 基于仓内追溯矩阵（R-01~R-27）、代码落地与自动化回归（static_scan + pytest），当前仓库在“文档声明范围内”呈现为**已实现并可回归**状态。  
- 你特别关注的能力项（微信对话、ClawdBot、VTS Live2D、LangGraph 统一治理、ASR/TTS、World/Style Canon 导入同步、双端 RPC+HTTP、人格成长）均已有独立模块与审计落点。  
- 同时你指出的关键问题成立：**当前系统缺少可视化运行界面，难以实时监控 agent 链路细节**。该问题属于“可观测呈现层缺口”，不是治理链路本身缺失。

## 2. 需求实现完成度判定口径
本报告“是否实现”的判定以三层证据为准：
1) 追溯矩阵：`docs/60_trace/requirements_trace.md` 的 DONE 状态。  
2) 代码存在性：对应模块、契约、错误码与审计字段可定位。  
3) 回归可验证性：静态扫描与测试可通过。  

> 注意：这是“仓内工程完成度”判定，不等价于“线上所有外部服务实时可用”。

## 3. 重点能力实现详解

### 3.1 微信对话（WeChat Channel）
实现位置：`host_brain/channels/wechat.py`。

实现细节：
1. **入站标准化**：`receive_wechat_event` 将多源字段标准化为 `message_id/from_user_id/to_user_id/thread_key/content`。  
2. **重复消息抑制**：通过 `ledger.has_channel_message` 检查 ingress 重复，重复时标记 `DUPLICATE_IGNORED` 与 `CHANNEL_DUPLICATE_MESSAGE`。  
3. **出站发送留痕**：`send_wechat_message` 对 ack 成功/失败统一落 `channel_io_log`，失败默认 `CHANNEL_SEND_FAILED`。  
4. **线程键确定性**：`build_thread_key` 固化 `wechat::<from>::<to>`，保障串联回放稳定。

### 3.2 ClawdBot（UI 自动化）
实现位置：`host_brain/skills/impl/clawd_ui_action.py`。

实现细节：
1. **置信度门槛**：低于阈值直接拒绝，返回 `UI_CONFIDENCE_LOW`。  
2. **红线熔断**：`redline_detected=True` 直接拒绝，返回 `VISUAL_REDLINE`。  
3. **元素存在性保护**：目标缺失触发 `UI_ELEMENT_NOT_FOUND`。  
4. **证据链闭环**：动作前后产物 `before_artifact_ref/after_artifact_ref` + `vault manifest` 绑定；manifest 失败触发 `MANIFEST_INVALID` 路径。  
5. **审计落库**：`ledger.log_clawd_action` 写入 confidence、artifact_ref、manifest_ref、reason_code、stage。

### 3.3 VTS Live2D 控制
实现位置：`host_brain/channels/vts.py`。

实现细节：
1. **talk gate 固定动作**：`request_talk_gate` 仅接受固定门控动作，非法动作拒绝。  
2. **emotion 枚举映射**：`request_emotion_expression` 仅允许 `emotion_tag -> expression_id` 映射表，不允许自由参数注入。  
3. **参数旁路拦截**：`raw_expression_id/raw_action_params` 存在时直接拒绝，防止模型自由控制底层动作。  
4. **审计落库**：`ledger.log_avatar_action` 记录 trigger/emotion_tag/expression_id/status/reason_code/stage。

### 3.4 LangGraph 对 Agent 的统一治理
实现位置：`host_brain/integrations/graph_runtime.py`（图编排运行时）；治理入口联动 `governor/*` 与 `tools/broker.py`。

实现细节：
1. **图结构校验**：运行前做节点/边配置合法性检查，缺失节点映射 `ORCH_NODE_MISSING`。  
2. **依赖可用性门控**：节点执行前检查 dependency 状态，不可用时输出 `ORCH_DEPENDENCY_UNAVAILABLE` 并走 fallback 边。  
3. **失败分类稳定化**：超时、依赖不可用、节点缺失等统一 reason_code/stage 输出。  
4. **运行态封装**：输出包含 graph_status/reason_code/stage/fallback_map/dependency_statuses，便于审计回放。

### 3.5 ASR 语音识别与语音生成（TTS）
实现位置：
- RPC 协议层：`edge_client/rpc/voice.py`  
- 执行层：`edge_client/actuators/speech.py`  
- 兜底模型约束：`edge_client/rpc/fallback_llm.py`

实现细节：
1. **语音事件化**：ASR chunk / speak / barge-in 为显式事件契约，具备 request 与时序字段。  
2. **打断语义**：barge-in 作为一等事件处理，避免“播放中不可打断”的黑盒行为。  
3. **TTS 执行落点**：speech 执行模块对输出、失败、状态进行结构化封装。  
4. **Fallback LLM 限权**：默认未加载、draft-only，且禁止 decision/confirm/approval 等裁决字段与“已执行”宣称。

### 3.6 World Canon 与 Style Canon 数据导入/同步
实现位置：
- `host_brain/memory/canon_world_store.py`  
- `host_brain/memory/canon_style_store.py`  
- `host_brain/memory/canon_sync.py`

实现细节：
1. **版本化存储**：Canon 条目按 version/hash 管理，支持稳定索引。  
2. **索引同步**：`canon_sync` 将文件侧状态与 ledger 索引对齐，并记录 proposal/change。  
3. **漂移检测**：提供版本/hash drift 检测，发现“文件与索引不一致”场景。  
4. **事实隔离**：Canon 为叙事/设定来源，不直接充当“已执行事实”。

### 3.7 双端 RPC 与 HTTP 通信
实现位置：
- Host 侧 HTTP/服务编排入口：`host_brain/app/main.py`  
- Edge 侧 RPC：`edge_client/rpc/*.py`

实现细节：
1. **Host HTTP 聚合层**：`build_service_health_report`、`build_startup_report`、`build_env_readiness_report` 输出服务健康、依赖探针、节点部署、图运行态汇总。  
2. **Edge RPC 能力面**：voice/health/resource_gate/fallback_llm 等端点形成受控能力接口。  
3. **跨端一致口径**：都采用 reason_code/stage/status 的结构化表达，便于双端故障对齐。  
4. **冷启动可观测**：即使 key/依赖缺失，仍输出可解析报告并标注降级原因。

### 3.8 人格成长（状态演化）
实现位置：`host_brain/core/metabolism.py`，并与 bond/memory/canon 形成上层联动。

实现细节：
1. **硬状态模型**：`energy_level/mood_state/sys_mode` 组成基础人格状态。  
2. **归一化与边界**：energy 限幅、mood/sys_mode 规范化，避免状态漂移失控。  
3. **事件驱动演化**：基于交互事件与 delta 更新状态（如 reward/success 对情绪与能量的影响）。  
4. **治理快照输出**：`build_governance_snapshot` 为 Governor 提供可决策上下文（energy_bucket/mood/sys_mode）。

## 4. 链路可视化现状与缺口（针对你的反馈）
### 4.1 现状（有数据、无可视化面板）
当前系统已有完整链路数据源，但主要通过日志/表/回放工具消费：
- 审计链：intent/governor/skill_exec/channel/avatar/clawd_action/service_health 等日志表。  
- 运行态摘要：startup/readiness/graph runtime 的结构化输出。  
- 回放能力：可基于 request_id/intent_id 串联链路进行排障。

### 4.2 缺口（你指出的问题）
- 没有统一 Web 可视化界面展示“单次请求从 LLM 草案→Governor→Tool/Skill→Audit”的完整轨迹。  
- 缺少实时态面板（当前节点、失败 stage、reason_code 分布、依赖可用性热图）。  
- 跨端（Host/Edge）事件时间线没有统一前端视图，排障依赖人工查表。

### 4.3 建议的最小可落地方案（不破坏现有不变量）
1. **MVP 监控页（只读）**：新增只读观测 API + 简单前端页面，按 request_id 展示 Triptych 时间线。  
2. **链路详情页**：展示 gate 命中、token 校验、tool 执行、证据 refs、最终输出状态。  
3. **聚合看板**：按 reason_code/stage 聚合失败分布，支持时间窗口筛选。  
4. **跨端关联**：以 request_id/thread_key/session_id 作为关联键，拼接 Host 与 Edge 事件。

> 约束：可视化层只读，不得新增任何旁路执行入口；仍以 Governor/Token/Audit 为唯一事实来源。

## 5. 当前风险与改进建议（围绕你关心的能力）
1. **跨通道 E2E 仍可加强**：建议补“微信入站→Governor→VTS/语音/ClawdBot”的一体化场景回归。  
2. **外设/外部服务依赖差异**：VTS、语音设备、第三方 provider 在不同环境的可用性差异需专项验收。  
3. **高复杂模块拆分**：graph_runtime、retrieval、ledger 建议继续按契约层/执行层拆分，降低耦合。  
4. **可视化缺口优先级提升**：建议将“运行链路监控 UI（只读）”提升为下一轮 P0/P1 的明确任务。

## 6. 最终回答（对你的问题的直接答复）
- **是否实现了白皮书及需求文档要求？** 在仓内追溯与回归口径下，答案是：**是，已实现并可回归**。  
- **当前确实没有可视化界面监控链路细节吗？** 是，问题成立；当前以审计与回放为主，缺少统一可视化面板。  
- **下一步最关键动作是什么？** 建立只读链路监控 UI，将现有 audit/runtime 数据可视化，提升定位效率与运维可观测性。
