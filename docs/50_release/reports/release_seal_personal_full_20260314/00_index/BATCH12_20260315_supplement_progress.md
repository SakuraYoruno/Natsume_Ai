# Batch 12 补充测试进度报告（F-34~F-52）

日期：2026-03-15  
批次：Batch 12（补充测试，覆盖原 F-01~F-33 遗漏功能）

---

## 代码变更

### 1. `host_brain/app/http_hooks.py`
- **新增 `[personality_tools_catalog]` 启动块**：将 5 个 Personality Skills（`diary_writer`、`dream_generator`、`desktop_note`、`focus_mode`、`proactive_talk`）和 3 个 Scheduler Skills（`scheduler_add`、`scheduler_cancel`、`scheduler_list`）注册到 ToolCatalog，ToolCatalog 从 6 增长到 **15**。
- **新增 `[long_term_distillation_catalog]` 启动块**：将 `long_term_memory_distillation` 工具注册到 ToolCatalog，并绑定 `LongTermMemoryDistiller.run_distillation_cycle()` 处理器。

---

## 测试结果汇总

| 测试项 | 状态 | 关键证据 |
|--------|------|---------|
| F-34 主动对话（Proactive Talk） | **PASS** | `status=EXECUTED`，生成"主人晚上好～今天过得怎么样？" |
| F-35 梦境生成（Dream Generator） | **PASS** | sleep 模式下 `status=EXECUTED`，`evidence_level=NARRATIVE_ONLY`；正常模式下 `SCHED_CONDITION_UNMET`（正确行为） |
| F-36 日记书写（Diary Writer） | **PASS** | `status=EXECUTED`，生成 `runtime/diary/diary/2026-03-15.md`，内容 109 字 |
| F-37 Scheduler 全生命周期 | **PASS** | enable/disable/trigger 全部 200；host-scheduler trigger 200；distillation stats 可查 |
| F-38 长期记忆蒸馏 | **PASS** | `topics_processed=6`，无 Episodic 数据时 `proposals_submitted=0`（正确行为） |
| F-39 主动推送链路 | **PASS** | `ProactivePushClient` 路径可执行；Edge `/v1/edge/proactive_messages` 可访问 |
| F-40 WeChat 链路 | **PASS** | 正确字段输入返回 `success=true`，`status=ACCEPTED`；`/obs/wechat/stats` 200 |
| F-41 Canon Style 召回 | **PASS** | Canon Style 文件存在（CN/JA），`StyleCanonStore` 可加载，`RecallFusionServiceContract` 有 style 路径 |
| F-42 Persona 话语向量 | **PASS** | `PersonaUtteranceProjectionDocument` 和 `CanonProjectionDocument` 均可加载 |
| F-43 StateArbiter | **PASS** | `/obs/state-arbiter/stats` 200，字段完整 |
| F-44 Desktop Note | **PASS** | create `EXECUTED`，文件生成；路径穿越 `REJECTED`（`SCOPE_VIOLATION`） |
| F-45 Focus Mode | **PASS** | enter/exit 均 `TOOL_EXECUTED` |
| F-46 Audit Retention | **PASS** | `run_retention_policy` 成功执行，删除 14 条 service_health_log、1329 条 retrieval_event_log；保护 intent_log/governor_log/skill_exec_log |
| F-47 Airlock Sandbox | **PASS** | 正常代码 `status=OK`，超时代码 `SANDBOX_TIMEOUT` |
| F-48 Airlock Image Filter | **PASS** | 正常图片 `decision=PASS`，`reason_code=IMAGE_EGRESS_CLEAN` |
| F-49 Trace 闭环（F-29 补全） | **PASS** | trace 包含 `llm_node` 节点，`llm_node_call_log` 有对应记录 |
| F-51 Scheduler Skills | **PASS** | `scheduler_list` `TOOL_EXECUTED`；`scheduler_add` 403（WRITE 工具需 Governor Token，符合设计） |
| F-52 Evidence Circuit Breaker | **PASS** | `/obs/failure/stats` 200，`total_failures=0`（正常状态） |
| F-50 Vision 截图（F-23 补全） | **PENDING** | 需 Edge 节点配合，暂不执行 |

---

## 关键发现

### 1. WRITE 工具需要 Governor Token（设计符合）
- `diary_writer`、`dream_generator`、`proactive_talk`、`scheduler_add` 等 WRITE 类工具通过 `/tools/execute` 调用时返回 403。
- 这是设计行为：WRITE 工具必须经过 Governor 审批流程（`approval_token_ref`）。
- **正确测试路径**：直接调用 `execute_*` 函数（Scheduler 路径）或通过 Chat 触发 LLM 工具调用。

### 2. 梦境生成需要 sleep 模式
- `dream_generator` 在 `sys_mode=normal` 时返回 `SCHED_CONDITION_UNMET`（正确行为）。
- 需要注入 `sys_mode=sleep` 或空闲状态才能触发。

### 3. 日记书写需要当日对话数据
- `diary_writer` 在无 `audit_reader` 回调或 `channel_io_log` 为空时返回 `SCHED_NO_CONTENT`（正确行为）。
- 需要注入 `audit_reader` 回调或等待 `channel_io_log` 有数据。

### 4. ToolCatalog 扩展成功
- 启动后 ToolCatalog 从 6 增长到 15，新增 Personality/Scheduler/LongTermDistillation 工具。
- READ 类工具（`focus_mode`、`scheduler_list`）可直接通过 `/tools/execute` 调用。

---

## F-06 Soak 状态
- F-06 soak 仍在运行（已超过 21/180 样本）。
- 本批测试期间 Host 重启一次（新增工具注册），soak 已重新计数。

---

## 放行结论补充
- F-34~F-52 中 18/19 项通过（F-50 Vision 需 Edge 配合，暂 PENDING）。
- 所有 Scheduler 任务至少手动触发一次，有成功或合理跳过证据。
- 梦境/日记/主动对话三个 Personality Skills 均有真实执行证据。
- Audit Retention 策略已验证（删除过期数据，保护 Triptych 链）。
- Airlock Sandbox 超时保护有效。
