# Natsume_Ai 架构优化详细 TodoList（基于 ARCH_REVIEW_AND_ROADMAP_2026-03-09）

**版本**：v1.0  
**创建日期**：2026-03-09  
**来源文档**：`docs/60_trace/ARCH_REVIEW_AND_ROADMAP_2026-03-09.md`  
**目标**：把路线图转为可执行任务清单，支持直接开工与跟踪。

---

## 实施进度（2026-03-09）

- [x] P0-A：流式路径在 LLM 调用前等待 Retrieval 结果，并注入 prompt/ContextPack
- [x] P0-A：新增流式 Retrieval 超时门控 `HOST_STREAM_RETRIEVAL_TIMEOUT_MS`（超时降级不阻塞）
- [x] P0-A：流式 Retrieval summary 字段与非流式对齐（`route_rows` / `route_errors` / `reason_chain`）
- [x] P0-A：新增 2 条单元测试覆盖“前置注入”和“超时降级”
- [x] P0-B：流式链路接入 VTS 情感驱动（限流 + 超时 + 开关），并写入 `COMMIT.audit_refs.vts`
- [x] P0-B：新增 1 条单元测试覆盖 `vts` 审计字段写入
- [x] P0-A：新增基线采样脚本 `tools/diag_stream_baseline.py`（首事件/首渲染/提交时延）
- [x] P1-C：新增 Graph 入口 SceneRouter handler（entry/post-core 双节点路由）
- [x] P1-C：`/v1/chat` 移除 I/O 层 pre-classifier 路由，改为 `scene_type=AUTO` 交由 Graph 入口决策
- [x] P1-C：`get_graph_runtime()` 注册 SceneRouter handlers，完成运行时接入
- [x] P1-B：Gaming CHAT 路由接入 Edge fallback 3B（`/v1/chat`），失败时自动回退 Host Graph
- [x] P1-B：最小能力约束落地（极简回复、跳过 retrieval/episodic，保留审计）
- [x] P1-B：新增 3 条单元测试覆盖 Edge 成功分流、失败兜底与开关关闭
- [x] P1-A：非 Gaming 默认路由接入 Host 8B（`chat_direct_ja`），复杂节点保持 Cloud
- [x] P1-A：新增 Host 8B 健康检查 + 不可用自动降级 Cloud + `HOST_CHAT_FORCE_CLOUD` 回滚开关
- [x] P1-A：新增 `sys_mode=gaming` 自动退出 Host 8B 显存门控
- [x] P1-A：新增 5 条单元测试覆盖默认分流、Gaming 门控、强制回滚、健康降级与节点提供方分离
- [x] P1-A：新增 A/B 评估工具 `tools/diag_chat_ab.py`（host8b/edge3b/cloud 预设、JSON+Markdown 报告）
- [x] P1-A：补齐 Ollama OpenAI 兼容基址透传（Host 8B 独立 `base_url`，不影响 Cloud provider）
- [x] P1-A：A/B 工具修复（metrics 兼容 + 会话序列去冲突），完成 `qwen3:8b` 实机冒烟报告
- [x] P1-C：统一路由决策对象 `route_decision`（host/edge/cloud/skills）贯通 SceneRouter、`/v1/chat` 与 `/v1/chat/stream` 审计输出
- [x] P0-A：完成流式链路基线采样文档 `baseline_stream_metrics_2026-03-09.md`（20 样本、可复现步骤）
- [x] P0-A：新增 `RetrievalOrchestrator`（统一 `query/session_state/trace_id -> retrieval_bundle` 协议）
- [x] P0-A：`/v1/chat` 与 `/v1/chat/stream` 接入编排器，覆盖 success/partial_failure/all_failure 路径
- [x] P0-A：六路召回契约回归通过（`test_recall_fusion_six_store_routes` + `test_retrieval_pipeline_contract`）
- [x] P0-A/A4：补齐 `context_pack_observability` 字段（来源计数/条目统计/上下文大小/召回路径）
- [x] P0-A/A5：流式与非流式路径增加退化顺序日志（`six_route -> core_three -> minimal_context -> bare_llm`）
- [x] P0-A/A5：短会话检索缓存（`RetrievalOrchestrator` TTL/LRU）落地，命中信息写入 `retrieval_summary`
- [x] P0-B/B1：新增统一 `emotion_snapshot`（tag/intensity/source/timestamp/age）并贯通 `chat`/`chat/stream` 审计输出
- [x] P0-B/B1：Prompt 绑定补齐情感强度与快照字段（`emotion_intensity` + `snapshot_*`），新增对应单测
- [x] P0-B/B3：新增闭环校验工具 `tools/diag_emotion_closed_loop.py`（12 条语料，输出 JSON+Markdown 报告）
- [x] P0-B/B3：完成首轮闭环报告 `EMOTION_CLOSED_LOOP_REPORT_20260309_235917.*`，一致性通过率 `100%`（12/12）
- [x] P0-A/A6：新增流式 SSE 契约测试 `test_stream_chat_contract.py`（GET/POST 事件顺序、终止条件、COMMIT 审计字段）
- [x] P0-A/A6：完成流式集成回归（`/v1/chat/stream` 端点层）并验证 intent_draft 透传
- [x] P0-A/A6：完成扩展回归 `45 passed`（含 stream/retrieval/chat 路径）
- [x] 5.2：新增路由器场景分类测试集 `test_scene_router_classification_dataset.py`（入口/后置路由、优先级与显式覆盖）
- [x] 5.2：完成场景分类回归扩展 `79 passed`（scene_router + stream + retrieval + chat）
- [x] 5.2：新增情感层回归样本集 `tests/fixtures/emotion/closed_loop_cases.json`（16 样本，覆盖 mood/tag 约束与强度 clamp）
- [x] 5.2：闭环工具支持 fixture 语料加载（`--case-file`）并在报告中记录 `case_source`
- [x] 5.2：完成情感层回归扩展 `82 passed`（scene_router + stream + retrieval + chat + emotion）
- [x] P2-C：新增统一诊断入口 `tools/diag.py`（子命令：`retrieval` / `stream` / `emotion` / `graph`）
- [x] P2-C：统一诊断报告格式落地（`DIAG_UNIFIED_<DOMAIN>_*.json/.md`）
- [x] P2-C：新增工具单测 `test_diag_unified_tool.py`（dry-run/参数拼装/下游报告解析）
- [x] P2-C：完成扩展回归 `86 passed`（含 unified diag + scene_router + stream + retrieval + chat + emotion）
- [x] P2-B：完成 `host_brain/obs` 与 `host_brain/observability` 能力清点并建立统一主路径（`observability`）
- [x] P2-B：将 metrics/session/trace/health/routes 主实现迁移到 `host_brain/observability/*`，`host_brain/obs/*` 保留兼容转发层
- [x] P2-B：应用层调用方切换到 `host_brain/observability/*`（dependencies/health_routes/system_handler/obs_routes）
- [x] P2-B：新增兼容导入单测 `test_observability_module_compat.py`，完成扩展回归 `149 passed`
- [x] 5.1：补齐关键链路 trace 字段（`retrieval_latency_ms` / `first_token_latency_ms` / `background_tasks_latency_ms`）
- [x] 5.1：`/v1/chat` 与 `/v1/chat/stream` 审计输出增加 `audit_refs.trace_metrics`（含 retrieval、首 token、后处理耗时）
- [x] 5.1：完成扩展回归 `149 passed`（obs + scene_router + stream + retrieval + chat + emotion）
- [x] 5.1：`/obs/metrics` 增加超时/降级/失败率阈值告警（环境变量阈值 + 稳定告警码）
- [x] 5.1：新增/更新 observability 契约回归 `26 passed`（`test_obs_metrics_contract` + `test_perf_baseline_contract`）
- [x] 5.1：新增周稳定性报告工具 `tools/diag_stability_weekly.py` 并接入 `tools/diag.py stability` 子命令
- [x] 5.1：生成首份周报 `WEEKLY_STABILITY_REPORT_20260310_005912.{json,md}`（7 天窗口，状态 `WARN`，2 条告警）
- [x] 5.3：新增灰度门控（按会话/用户哈希百分比 + 场景白名单 + allowlist），`/v1/chat` 与 `/v1/chat/stream` 已接入
- [x] 5.3：灰度回归通过 `15 passed`（host8b runtime route + stream/chat 路径）
- [x] 5.3：新增发布门禁脚本 `tools/release_rollback_gate.py`（绑定回滚条件 + 回滚命令输出）
- [x] 5.3：新增回滚运行手册 `ROLLBACK_RUNBOOK_2026-03-10.md`，并生成门禁报告 `RELEASE_ROLLBACK_GATE_20260310_010648.{json,md}`
- [x] 5.3：新增 24h 复盘脚本 `tools/post_release_review.py`（聚合稳定性 + 回滚门禁结论）
- [x] 5.3：生成首份复盘 `POST_RELEASE_REVIEW_20260310_010918.{json,md}`（`release_id=codex-20260309`，状态 `COMPLETE`）
- [x] P2-A：`skills/registry.py` 新增统一 `@skill` 装饰器 + `register_decorated()` 扫描注册入口
- [x] P2-A：补齐 `SkillResult` 与 `permission_model` 抽象，新增装饰器回归 `60 passed`（含 skills/governance）
- [x] P2-A：新增 IDE Skills 封装 `ide_registry.py`（`open_cursor` / `run_pycharm_action` / `codex_task`）
- [x] P2-A：新增 MCP→Skills 桥接 `mcp_skill_bridge.py`（统一 `mcp::<provider>::<tool>` 命名，支持 broker 单入口调用）
- [x] P2-A：新增 skills/mcp 相关回归 `26 passed`（decorator + ide + mcp bridge + hot_loader）

---

## 0. 执行规则（先统一）

- [ ] 统一分支策略：`feat/p0-stream-retrieval`、`feat/p0-stream-emotion`、`feat/p1-scenerouter` 等
- [ ] 每个任务必须有：代码改动 + 测试 + 观测指标 + 回滚方案
- [ ] 每日同步四项：进度、阻塞、风险、次日计划
- [ ] 严格区分“首字节路径”和“后台异步路径”，禁止后台写入阻塞 SSE 输出

---

## 1. P0（立即执行）最大功能缺口修复

### P0-A 流式 Chat 接入六路召回（预估 3-5 天）

#### A1. 现状梳理与基线测量（0.5 天）
- [x] 梳理当前链路：`/v1/chat/stream -> chat_stream_handler -> astream_llm_chunks`
- [x] 记录基线指标：首字节延迟、完整响应耗时、召回命中率（当前应为空）
- [x] 输出链路图与瓶颈点（文档化）

**产出**
- `docs/60_trace/baseline_stream_metrics_YYYY-MM-DD.md`

**验收标准**
- 有可复现实验步骤和至少 20 条样本统计

#### A2. 召回编排器设计（0.5 天）
- [x] 设计 `RetrievalOrchestrator` 接口（并发六路召回 + 超时控制 + 失败降级）
- [x] 统一输入输出协议：query、session_state、retrieval_bundle、trace_id
- [x] 确定超时预算（建议总预算 200ms，单路超时 120-180ms）

**产出**
- 设计文档 + 接口定义（类型注解）

**验收标准**
- 能清晰描述“成功/部分失败/全失败”三种路径

#### A3. 六路召回接入实现（1-1.5 天）
- [x] 接入 Episodic 向量召回
- [x] 接入 StyleCanon 检索
- [x] 接入 WorldCanon 检索
- [x] 接入 Bond 摘要读取
- [x] 接入全文检索
- [x] 接入重排融合（Reranker/Fusion）

**影响模块（预计）**
- `host_brain/app/chat_stream_handler.py`
- `host_brain/app/chat_context_builder.py`
- `host_brain/retrieval/*`
- `host_brain/memory/*`
- `host_brain/bond/*`

**验收标准**
- 六路结果可在同一 `retrieval_bundle` 中观测到
- 单路失败不导致整体流式失败（降级可用）

#### A4. ContextPack 前置构建（0.5-1 天）
- [x] 在进入 LLM 流式调用前构建 ContextPack
- [x] 注入 prompt payload，保证召回结果参与生成
- [x] 限制上下文大小，避免 token 爆炸

**验收标准**
- 日志可见 ContextPack 来源与条目统计
- 与“裸 LLM”相比，回答可引用召回内容

#### A5. 性能与退化策略（0.5 天）
- [x] 增加并发执行与超时取消策略
- [x] 增加缓存（可选）：短会话窗口内重复 query 复用召回
- [x] 定义退化顺序：`六路 -> 核心三路 -> 最小上下文 -> 裸 LLM`

**验收标准**
- 首字节 P95 控制在目标范围（建议 <= 500ms）
- 退化路径有明确日志标识

#### A6. 测试与回归（0.5 天）
- [x] 单元测试：编排器、超时、融合逻辑
- [x] 集成测试：流式路径能带召回上下文输出
- [x] 回归测试：非流式路径不被破坏

**验收标准**
- 新增测试通过；关键回归项通过

---

### P0-B 流式 Chat 接入情感层（预估 1-2 天）

#### B1. 情感快照接入（0.5 天）
- [x] 在召回后、LLM 前调用 `emotion_context` 构建快照
- [x] 将情感状态注入 prompt（含强度/标签）

**验收标准**
- trace 中可观测 `emotion_snapshot`
- prompt 构建包含情感字段

#### B2. VTS 驱动事件打通（0.5 天）
- [x] 将情感标签映射为 VTS 事件
- [x] 事件异步投递，不阻塞 SSE

**验收标准**
- SSE 输出不中断；VTS 事件单独可追踪

#### B3. 闭环校验（0.5 天）
- [x] 对 10+ 条情感测试语料做验证
- [x] 校验“情感识别 -> 输出语气 -> VTS 动作”一致性

**验收标准**
- 一致性通过率达到预设阈值（建议 >= 80%）

---

## 2. P1（近期）体验提升任务

### P1-A Host 部署 Qwen3 8B（预估 2-3 天）
- [x] 完成模型规格选型（优先 Q4_K_M）
- [x] 完成推理服务启动/健康检查/降级策略
- [x] 建立显存门控：`sys_mode=GAMING` 自动退出 Host 8B
- [x] 对比 3B 与 8B 质量和延迟（A/B 样本集）

**验收标准**
- 非 Gaming 场景默认走 Host 8B
- 有可回滚到 Cloud 的开关

### P1-B Gaming 切换 Edge 3B（预估 1-2 天）
- [x] 将 Gaming CHAT 路由改为 Edge 3B（替代模板退化）
- [x] 限制能力：极简回复、基础 TTS、不触发复杂动作
- [x] 增加切换日志与失败兜底

**验收标准**
- Gaming 下稳定走 Edge 3B
- 显存冲突明显下降

### P1-C SceneRouter 下沉到 Graph 入口（预估 2 天）
- [x] 从 FastAPI handler 移除 `pre_intent_classifier`
- [x] 新增 Graph 第一节点：`SceneRouterNode`
- [x] 输出统一路由决策对象（host/edge/cloud/skills）

**验收标准**
- FastAPI 仅做 I/O，不再持有路由决策
- 路由策略可单测

---

## 3. P2（中期）架构治理优化

### P2-A Skills 层 OpenClaw 风格封装（预估 5-7 天）
- [x] 设计统一 `@skill` 注册器
- [x] 抽象 `SkillRegistry`、`SkillResult`、权限模型
- [x] 封装 Cursor / PyCharm / Codex 三类能力
- [x] 打通 MCP 工具注册与 Skills（单入口）

**验收标准**
- 新增技能可通过统一协议发现与调用
- MCP/Skills 不再双轨维护

### P2-B observability 合并（预估 1 天）
- [x] 清点 `obs/` 与 `observability/` 重复能力
- [x] 合并目录和导出路径
- [x] 迁移调用方，保留兼容层（短期）

**验收标准**
- 功能不回退；目录职责清晰

### P2-C 诊断工具整合（预估 1-2 天）
- [x] 合并 `tools/_diag_*.py` 为统一入口命令
- [x] 提供子命令：`retrieval`、`stream`、`emotion`、`graph`
- [x] 输出统一报告格式（JSON + 人类可读）

**验收标准**
- 常见诊断场景无需查找多个脚本

---

## 4. P3（长期）闭环建设

- [ ] Bond 更新统一异步队列化
- [ ] 情感层接入 Host 8B / Edge 3B / Cloud Graph 全路径
- [ ] MCP 与 Skills 完全合并，形成唯一工具入口
- [ ] 建立长期指标看板（质量、延迟、成本、可用性）

---

## 5. 跨阶段公共任务（必须持续执行）

### 5.1 观测与告警
- [x] 增加关键链路 trace：召回耗时、首 token、后台任务耗时
- [x] 为超时、降级、失败率设置阈值告警
- [x] 每周输出稳定性报告

### 5.2 测试体系
- [x] 为流式链路增加契约测试（SSE 事件顺序、终止条件）
- [x] 为路由器增加场景分类测试集
- [x] 为情感层增加回归样本集

### 5.3 发布与回滚
- [x] 每个阶段准备灰度开关（按用户/按场景）
- [x] 每次上线绑定回滚条件与回滚命令
- [x] 发布后 24h 做一次复盘

---

## 6. 建议排期（可直接执行）

### 周 1（P0）
- [ ] 完成 P0-A（六路召回 + ContextPack + 测试）
- [ ] 完成 P0-B（情感层 + VTS 异步）

### 周 2（P1）
- [x] 完成 Host 8B 部署与评估
- [x] 完成 Gaming -> Edge 3B 切换
- [ ] 完成 SceneRouter 下沉到 Graph 入口

### 周 3-4（P2）
- [ ] Skills 统一封装
- [ ] 可观测模块合并
- [ ] 诊断工具统一入口

### 周 5+（P3）
- [ ] 异步队列与全路径闭环
- [ ] 长期指标治理

---

## 7. 任务完成定义（DoD）

- [ ] 代码合入主分支并通过 CI
- [ ] 对应测试补齐并通过
- [ ] 有监控指标和日志字段
- [ ] 文档更新（架构图、运行手册、回滚说明）
- [ ] 业务验收通过（质量/延迟/稳定性达标）

---

## 8. 当前建议立即开工项（今天）

- [ ] 开分支：`feat/p0-stream-retrieval`
- [ ] 完成 P0-A 的 A1/A2（现状梳理 + 编排器设计）
- [ ] 输出第一版接口草图并评审
- [ ] 明确 P0 验收指标阈值并固化到文档
