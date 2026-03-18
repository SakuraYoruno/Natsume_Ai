受众：MASTER
标题：Natsume AI 封测计划补充（F-34 ~ F-52）

# Natsume AI 封测计划补充（F-34 ~ F-52）

更新时间：2026-03-17  
状态：补充版，与 `release_seal_test_plan_20260313.md` 配套使用  
说明：原计划 F-01~F-33 未覆盖以下已启用功能模块。本文补充所有缺失测试项，并要求对未启动的定时任务/服务先手动触发再测试。**多智能体专项封测**（execution_profile 选图、Supervisor+专家 Agent+Judge、观测与审计等）见主计划 §4 矩阵 MA-01～MA-06、§10。

---

## 补充背景

原计划 F-01~F-33 遗漏的已启用功能模块（代码已实现、Scheduler 已注册或 Skill 已注册）：

| 类别 | 遗漏功能 |
|------|---------|
| Personality Skills | `dream_generator`（梦境生成）、`diary_writer`（日记书写）、`proactive_talk`（主动对话） |
| Scheduler Tasks | `proactive_talk_periodic`（每 15 分钟检查）、`long_term_memory_distillation`（每日 03:00）、`distillation_engine_periodic`（每 30 分钟，host-scheduler） |
| Personality Skills（其他） | `desktop_note`（桌面留言）、`focus_mode`（专注模式）、`scheduler_add/cancel/list` |
| Memory 扩展 | Canon Style Store（风格 Canon）、Canon 向量投影、Persona 话语向量投影、长期记忆蒸馏（LongTermDistiller） |
| Channels | WeChat 链路（完整 callback→dispatch→reply→egress）、主动推送链路（Host→Edge proactive_push） |
| Retrieval 扩展 | StateArbiter 功能验证、Canon Style 召回路径 |
| Airlock 扩展 | Sandbox 代码执行、Image Filter 图片过滤 |
| Audit 扩展 | Retention 保留策略功能验证 |
| Trace 补全 | F-29 公开 trace 缺少 llm/graph/audit_snapshot 节点（原计划 BLOCK） |
| Vision 扩展 | F-23 截图与自动化（原计划 PENDING） |

---

## 补充阈值

| 项 | 阈值 |
|----|------|
| Scheduler 手动触发响应 | `EXECUTED` 或 `SKIPPED`（条件不满足时），不接受 `DEGRADED` |
| 梦境/日记生成 | 有真实 LLM 输出文本，写入 Episodic 可查，无臆造 |
| 主动对话推送 | Edge 侧 `/v1/edge/proactive_messages` 可拉取到消息 |
| WeChat 链路 | 消息收发完整，去重有效，audit 可查 |
| Canon Style 召回 | 风格 Canon 文档在召回结果中可见 |
| Retention 策略 | 过期数据被清理，Triptych 链保护数据不被误删 |
| Sandbox 执行 | 代码在隔离环境执行，超时/越权稳定拒绝 |

---

## F-34：主动对话（Proactive Talk）全链路

**范围**：`proactive_talk` Skill → Scheduler 触发 → LLM 生成问候 → ProactivePushClient → Edge `/v1/edge/proactive_push` → Edge 消息队列 → `/v1/edge/proactive_messages` 拉取

**真实检查方法**：
1. 确认 `proactive_talk_periodic` 任务已注册（`GET /scheduler/tasks`）
2. 手动触发：`POST /scheduler/tasks/proactive_talk_periodic/trigger`（跳过空闲阈值检查）
3. 或直接通过 `/tools/execute` 执行 `proactive_talk` skill（`idle_threshold_ms=0`）
4. 检查 Edge 侧 `GET /v1/edge/proactive_messages` 是否有消息
5. 检查 Host 侧 `/obs/scheduler/history` 是否有执行记录
6. 检查 audit.sqlite3 `channel_io_log` 是否有 proactive 推送记录

**通过标准**：
- Skill 执行返回 `status=EXECUTED`
- Edge 侧 `/v1/edge/proactive_messages` 返回非空消息列表
- 消息包含 `type/content/time_period/ts_ms` 字段
- Scheduler 历史可查到执行记录
- 禁用任务后触发数必须为 0

**等级**：阻断

---

## F-35：梦境生成（Dream Generator）全链路

**范围**：`dream_generator` Skill → 读取当日 Episodic Memory → LLM 生成梦境叙事 → 写入 Episodic（`dream` 标签 + `NARRATIVE_ONLY`）

**真实检查方法**：
1. 确认 `dream_generator` 已在 SkillRegistry 注册（`GET /tools/meta-pack` 或进程内查询）
2. 通过 `/tools/execute` 直接执行：
   ```json
   {"tool_id": "dream_generator", "args": {"max_episodic_entries": 5}}
   ```
3. 检查返回结果 `result_code=TOOL_EXECUTED`
4. 检查 `memory_ops_log` 是否有新写入的 dream 标签记录
5. 检查写入的 Episodic 记录 `evidence_level=NARRATIVE_ONLY`
6. 验证 dream 内容不会被 MemoryGuard 写入 EpisodicStore（NARRATIVE_ONLY 阻断）

**通过标准**：
- Skill 执行有成功证据（`EXECUTED` 或 `SKIPPED` 当无 Episodic 数据时）
- 有 Episodic 数据时：LLM 生成的梦境文本有实质内容（≥50字）
- 写入记录带 `dream` 分类标签
- `evidence_level=NARRATIVE_ONLY`，不会被误写入 EpisodicStore 主路径
- 无 `DEGRADED` 或未解释异常

**等级**：阻断

---

## F-36：日记书写（Diary Writer）全链路

**范围**：`diary_writer` Skill → 读取当日 audit/channel_io_log → LLM 生成日记叙事 → 双写文件 + Episodic Memory → 可选 drift_suggestion → Canon 提案

**真实检查方法**：
1. 确认 `diary_writer` 已在 SkillRegistry 注册
2. 通过 `/tools/execute` 直接执行：
   ```json
   {"tool_id": "diary_writer", "args": {"base_dir": "D:/Natsume_Ai/runtime/diary", "date": "2026-03-15"}}
   ```
3. 检查 `runtime/diary/` 目录下是否生成日记文件
4. 检查 `memory_ops_log` 是否有对应写入记录
5. 验证日记内容覆盖当日主要对话（不臆造）
6. 若有 drift_suggestion：检查 Canon 提案是否生成（`GET /canon/proposals`）

**通过标准**：
- Skill 执行返回 `EXECUTED`（有当日对话时）或 `SKIPPED`（无对话时，不伪造）
- 日记文件存在且内容可读（≥100字，有当日日期）
- Episodic 写入记录可查，带 `diary` 分类标签
- 无臆造内容（不捏造未发生的对话）
- 若触发 drift_suggestion，Canon 提案格式正确

**等级**：阻断

---

## F-37：Scheduler 任务全生命周期

**范围**：`proactive_talk_periodic`、`long_term_memory_distillation`（Scheduler）、`distillation_engine_periodic`（Host Scheduler）的 enable/disable/trigger/history 完整生命周期

**真实检查方法**：
1. 列出所有任务：`GET /scheduler/tasks`、`GET /host-scheduler/tasks`
2. 手动触发 `long_term_memory_distillation`：`POST /scheduler/tasks/long_term_memory_distillation/trigger`
3. 检查执行结果（`GET /scheduler/history` 或 `/obs/scheduler/history`）
4. 禁用任务：`POST /scheduler/tasks/proactive_talk_periodic/disable`
5. 验证禁用后不再触发（等待一个 interval 周期后检查）
6. 重新启用：`POST /scheduler/tasks/proactive_talk_periodic/enable`
7. 手动触发 Host Scheduler 蒸馏任务：`POST /host-scheduler/tasks/distillation_engine_periodic/trigger`
8. 检查蒸馏结果（`GET /obs/distillation/stats`）

**通过标准**：
- 所有任务 enable/disable/trigger 操作返回 200
- 手动触发后 `/obs/scheduler/history` 有执行记录
- 禁用后触发数必须为 0
- Host Scheduler 蒸馏触发后 `distillation/stats.total_cycles` 增加
- 无误触发、无卡死

**等级**：阻断

---

## F-38：长期记忆蒸馏（Long Term Distillation）

**范围**：`long_term_memory_distillation` Skill → 读取高频/MILESTONE Episodic → LLM 摘要 → World Canon 提案（含情感关系蒸馏）

**真实检查方法**：
1. 确认 `long_term_memory_distillation` 已在 SkillRegistry 注册
2. 通过 `/tools/execute` 直接执行：
   ```json
   {"tool_id": "long_term_memory_distillation", "args": {"top_k": 3, "max_proposals": 3}}
   ```
3. 检查返回结果
4. 检查 `GET /canon/proposals` 是否有新生成的提案
5. 检查 `/obs/distillation/stats` 的 `total_proposals_submitted` 是否增加
6. 检查提案内容是否来自真实 Episodic 数据（不臆造）

**通过标准**：
- Skill 执行返回 `EXECUTED`（有足够 Episodic 数据时）或 `SKIPPED`（数据不足时）
- 有数据时：生成的 Canon 提案内容与 Episodic 记录可对应
- 提案格式正确，`proposal_type` 合法
- `distillation/stats.total_proposals_submitted` 增加
- 无 `DEGRADED` 或未解释异常

**等级**：阻断

---

## F-39：主动推送链路（Proactive Push）

**范围**：Host `ProactivePushClient` → HMAC 签名 → Edge `POST /v1/edge/proactive_push` → Edge 内存队列 → `GET /v1/edge/proactive_messages` 拉取

**真实检查方法**：
1. 通过 `/tools/execute` 执行 `proactive_talk`（`idle_threshold_ms=0` 强制触发）
2. 检查 Host 侧 `channel_io_log` 有 proactive 推送记录
3. 在 Edge 节点侧调用 `GET /v1/edge/proactive_messages`（端口 17800）
4. 验证消息结构：`type/content/time_period/event_id/ts_ms`
5. 验证 HMAC 签名有效（伪造签名被拒绝）
6. 验证消息拉取后队列清空（再次调用返回空列表）

**通过标准**：
- Host → Edge 推送成功（Edge 返回 `success=true`）
- Edge `/v1/edge/proactive_messages` 返回消息
- 消息内容与 Skill 生成内容一致
- 伪造签名被稳定拒绝（401/403）
- 拉取后队列清空

**等级**：阻断

---

## F-40：WeChat 链路（完整收发）

**范围**：WeChat callback → `POST /v1/host/input`（source=WECHAT）→ Governor → Chat → Reply → egress → 去重保护

**真实检查方法**：
1. 检查 WeChat 通道是否已配置（`GET /obs/wechat/stats`）
2. 模拟 WeChat 消息输入：`POST /v1/host/input`，body 包含 `source=WECHAT`、`user_id`、`message`
3. 检查消息是否被正确路由到 Chat 主链路
4. 检查 `channel_io_log` 的 ingress/egress 记录
5. 模拟重复消息（相同 `msg_id`），验证去重有效（第二次被忽略）
6. 检查 `GET /obs/wechat/stats` 的统计数据

**通过标准**：
- WeChat 消息成功进入 Chat 主链路并得到回复
- `channel_io_log` 有完整 ingress/egress 记录
- 重复消息去重有效（误处理数为 0）
- 回复不丢失（egress 记录可查）
- `wechat/stats` 统计数据更新

**等级**：阻断（若 WeChat 未实际配置，需在结论中标注 N/A 并说明原因）

---

## F-41：Canon Style 召回路径

**范围**：Canon Style Store（`natsume_ai_cn.json`/`natsume_ai_ja.json`）→ 向量投影 → 七路召回中的 `route_canon_style` 路径 → 回答风格一致性

**真实检查方法**：
1. 检查 Canon Style 文件是否已加载（`GET /health/canon_sync`）
2. 发送一条明确需要风格一致性的 chat 请求（如"请用你平时的语气回答我"）
3. 检查 `/obs/recall-pack/stats` 的 `slots` 中是否有 `canon_style` 路径
4. 检查 `GET /obs/trace/{request_id}` 的召回节点是否包含 canon_style 证据
5. 验证回答风格与 Canon Style 文件中定义的风格一致

**通过标准**：
- Canon Style 文件已加载（canon_sync 健康）
- 召回包中 `canon_style` 路径有命中
- 回答风格与 Canon Style 定义一致（无明显风格偏离）

**等级**：阻断

---

## F-42：Persona 话语向量召回

**范围**：`persona_utterance_vector_projection.py` → 人格话语向量化 → 七路召回中的 `route_persona_utterance` 路径

**真实检查方法**：
1. 检查 Persona 话语向量是否已初始化（`GET /obs/semantic-recall/stats`）
2. 发送一条触发人格一致性检索的 chat 请求（如"你之前怎么说的？"）
3. 检查召回包中 `route_persona_utterance` 是否有命中
4. 验证人格话语向量投影不会引入错误的人格偏移

**通过标准**：
- Persona 话语向量已初始化
- `route_persona_utterance` 路径在召回包中可见
- 无人格偏移或串号

**等级**：阻断

---

## F-43：StateArbiter 状态仲裁

**范围**：`state_arbiter.py` → 多源状态优先级仲裁（WorldCanon > StyleCanon > Episodic > Bond > Chroma > Edge）

**真实检查方法**：
1. 检查 `GET /obs/state-arbiter/stats` 的仲裁统计
2. 构造一个存在状态冲突的场景（如 WorldCanon 和 Episodic 对同一事实有不同描述）
3. 验证仲裁结果符合优先级规则（WorldCanon 优先）
4. 检查仲裁结果在最终回答中的体现

**通过标准**：
- `state-arbiter/stats` 有仲裁记录
- 优先级规则正确执行（高优先级来源覆盖低优先级）
- 仲裁结果在 trace 中可追溯

**等级**：阻断

---

## F-44：Desktop Note（桌面留言）

**范围**：`desktop_note` Skill → 文件创建/更新/删除 → 路径穿越防护 → QA 校验

**真实检查方法**：
1. 执行 `desktop_note` create：
   ```json
   {"tool_id": "desktop_note", "args": {"action": "create", "filename": "test_note.txt", "content": "封测留言", "base_dir": "D:/Natsume_Ai/runtime/notes"}}
   ```
2. 验证文件已创建
3. 执行 update 操作，验证文件内容更新
4. 尝试路径穿越攻击（`filename: "../../etc/passwd"`），验证被拒绝
5. 执行 delete 操作，验证文件删除

**通过标准**：
- create/update/delete 操作均有成功证据
- 路径穿越攻击被稳定拒绝
- 文件操作有 audit 记录

**等级**：阻断

---

## F-45：Focus Mode（专注模式）

**范围**：`focus_mode` Skill → 进入/退出专注模式 → 抑制低优先级调度任务和主动对话

**真实检查方法**：
1. 执行 `focus_mode enter`：
   ```json
   {"tool_id": "focus_mode", "args": {"action": "enter", "duration_ms": 300000, "reason": "封测验证"}}
   ```
2. 检查返回结果
3. 在专注模式下尝试触发 `proactive_talk`，验证被抑制
4. 执行 `focus_mode exit`，验证恢复正常
5. 检查 audit 记录

**通过标准**：
- enter/exit 操作均有成功证据
- 专注模式下低优先级任务被抑制
- 退出后恢复正常触发

**等级**：阻断

---

## F-46：Audit Retention 保留策略

**范围**：`audit/retention.py` → 按表分级保留 → Triptych 链保护 → GC 清理过期数据

**真实检查方法**：
1. 检查 `GET /obs/retention/stats` 的保留策略统计
2. 检查各表的保留配置（`retention_days` 设置）
3. 手动触发 GC（若有触发接口）或等待定时触发
4. 验证过期数据被清理，Triptych 链保护的数据不被误删
5. 检查 GC 前后 `memory_ops_log` 等表的记录数变化

**通过标准**：
- `retention/stats` 有保留策略配置
- GC 清理后过期数据消失
- Triptych 链保护数据未被误删
- GC 操作有 audit 记录

**等级**：阻断

---

## F-47：Airlock Sandbox（代码沙箱执行）

**范围**：`airlock/sandbox.py` → DRAFT_CODE 场景 → subprocess/docker 隔离执行 → 超时/资源限制 → 越权拒绝

**真实检查方法**：
1. 通过 Chat 触发 DRAFT_CODE 场景（发送需要执行代码的请求）
2. 检查代码执行结果是否在隔离环境中运行
3. 测试超时场景（发送无限循环代码），验证超时拒绝
4. 测试越权场景（尝试访问系统文件），验证被拒绝
5. 检查 `sandbox_exec_log` 表的执行记录

**通过标准**：
- 正常代码在隔离环境执行成功
- 超时代码被稳定拒绝（不卡死主进程）
- 越权操作被稳定拒绝
- 所有执行有 `sandbox_exec_log` 记录

**等级**：阻断

---

## F-48：Airlock Image Filter（图片安全过滤）

**范围**：`airlock/image_filter.py` → 图片 Egress 过滤 → 敏感内容检测

**真实检查方法**：
1. 通过 Vision 场景触发图片输出
2. 检查图片过滤是否生效（正常图片通过，敏感图片被拦截）
3. 检查过滤结果在 `airlock_log` 中的记录

**通过标准**：
- 正常图片通过过滤
- 敏感图片被稳定拦截
- 过滤结果有 `airlock_log` 记录

**等级**：阻断（若 Vision 未启用图片输出，标注 N/A）

---

## F-49：F-29 补全——审计与 Trace 完整闭环

**范围**：补全原 F-29 BLOCK 项：公开 trace 补充 `llm_node_call_log`、`graph_run_trace`、`error_log(AUDIT_SNAPSHOT)` 节点；补充 tool/agent 同 `request_id` 链

**真实检查方法**：
1. 发送一条 chat 请求，获取 `request_id`
2. 检查 `/obs/trace/{request_id}` 是否包含 `llm_node` 类型节点
3. 检查 `llm_node_call_log` 表是否有对应记录（按 `request_id` 查询）
4. 检查 `graph_run_trace` 表是否有对应记录
5. 执行一个 tool 调用，检查同 `request_id` 的 trace 是否包含 tool 节点
6. 执行一个 agent run，检查同 `request_id` 的 trace 是否包含 agent 节点
7. 验证时序自洽（各节点时间戳单调递增）

**通过标准**：
- `/obs/trace/{request_id}` 包含 `llm_node` 节点
- `llm_node_call_log` 和 `graph_run_trace` 按 `request_id` 可查
- tool 调用和 agent run 在同 `request_id` trace 中可见
- 时序自洽，无断链

**等级**：阻断

---

## F-50：F-23 补全——Vision 截图与自动化（需 Edge 配合）

**范围**：`GET /v1/edge/screenshot` → 截图 → `POST /v1/agent/run`（VISION_UI 场景）→ ActionPlanner → ClawdBot 执行 → UI Assertion 验证

**真实检查方法**：
1. 在 Edge 节点侧确认 VTS/桌面可见
2. 调用 `GET /v1/edge/screenshot` 获取截图
3. 发送 VISION_UI 场景 chat 请求（含截图 base64）
4. 检查 ActionPlanner 生成的 ActionPlan
5. 检查 ClawdBot 执行结果
6. 检查 UI Assertion 验证结果（`GET /obs/ui-assertion/stats`）
7. Edge 节点电脑侧白盒确认：桌面动作真实可见

**通过标准**：
- 截图成功获取
- ActionPlan 生成合理
- ClawdBot 执行结果可查
- UI Assertion 验证有记录
- Edge 节点电脑侧动作真实可见

**等级**：阻断

---

## F-51：Scheduler Skills（scheduler_add/cancel/list）

**范围**：`scheduler_add`、`scheduler_cancel`、`scheduler_list` Skills → 通过 `/tools/execute` 执行 → Scheduler 任务增删改查

**真实检查方法**：
1. 执行 `scheduler_list`，验证返回当前任务列表
2. 执行 `scheduler_add`，添加一个测试任务：
   ```json
   {"tool_id": "scheduler_add", "args": {"task_name": "test_seal_task", "task_type": "custom", "trigger_type": "once", "trigger_config": {"trigger_at": "2099-01-01T00:00:00"}}}
   ```
3. 验证任务已添加（`GET /scheduler/tasks`）
4. 执行 `scheduler_cancel`，取消测试任务
5. 验证任务已删除

**通过标准**：
- list/add/cancel 均有成功证据
- 任务增删在 `/scheduler/tasks` 中可查
- 无副作用（测试任务不实际触发）

**等级**：阻断

---

## F-52：Evidence Circuit Breaker & Write Queue

**范围**：`evidence_circuit_breaker.py` → 证据写入熔断器；`evidence_write_queue.py` → 异步批写队列

**真实检查方法**：
1. 检查熔断器是否在正常状态（`GET /obs/failure/stats`）
2. 模拟高频写入，验证批写队列正常工作
3. 模拟写入失败，验证熔断器触发（`OPEN` 状态）
4. 等待熔断器自动恢复（`HALF_OPEN` → `CLOSED`）
5. 检查熔断状态在 `failure_events` 中的记录

**通过标准**：
- 正常状态下熔断器为 `CLOSED`
- 高频写入时批写队列正常工作，无数据丢失
- 写入失败时熔断器触发，有 `failure_events` 记录
- 熔断器自动恢复

**等级**：阻断

---

## 补充测试执行顺序

建议按以下顺序执行（可并行的批次）：

### Batch A（Scheduler/Skills 触发类，可立即执行）
1. F-37：Scheduler 任务全生命周期
2. F-34：主动对话全链路（手动触发）
3. F-35：梦境生成全链路（手动触发）
4. F-36：日记书写全链路（手动触发）
5. F-38：长期记忆蒸馏（手动触发）
6. F-51：Scheduler Skills（add/cancel/list）

### Batch B（记忆/召回扩展类）
7. F-41：Canon Style 召回路径
8. F-42：Persona 话语向量召回
9. F-43：StateArbiter 状态仲裁
10. F-46：Audit Retention 保留策略

### Batch C（工具/安全类）
11. F-44：Desktop Note
12. F-45：Focus Mode
13. F-47：Airlock Sandbox
14. F-48：Airlock Image Filter

### Batch D（链路补全类）
15. F-39：主动推送链路
16. F-40：WeChat 链路
17. F-49：F-29 补全（Trace 闭环）
18. F-50：F-23 补全（Vision 截图，需 Edge 配合）
19. F-52：Evidence Circuit Breaker & Write Queue

---

## 放行结论补充条件

在原 F-01~F-33 全部通过的基础上，还需满足：

1. F-34~F-52 中属于当前启用范围的项全部通过
2. 未启用的项（如 WeChat 未配置）需在结论中明确标注 `N/A` 并说明原因
3. 所有 Scheduler 任务至少手动触发一次，有成功或合理跳过（`SKIPPED`）证据
4. 梦境/日记/主动对话三个 Personality Skills 均有真实执行证据
5. 主动推送链路（Host→Edge）有完整端到端证据

---
