受众：MASTER
标题：Natsume AI 上线前封测计划（草案）

# Natsume AI 上线前封测计划（草案）

更新时间：2026-03-17
状态：`HISTORICAL_SUPERSEDED`。本文件保留 2026-03-17 的旧封测基线，不再作为当前生产能力或 NE-07 放行依据；现行验收以 v3.1、`tests/TESTPLAN.md` 与后续 `FullStackAcceptanceReceiptV1` 为准。

## 1. 扫描结果快照

### 1.1 代码规模

| 范围 | Python 文件数 |
|---|---:|
| `host_brain` | 248 |
| `edge_client` | 33 |
| `scripts` | 15 |
| `tests` | 342 |
| 总扫描文件（`host_brain/edge_client/scripts/tests`） | 1006 |

### 1.2 路由面

| 项 | 数量 |
|---|---:|
| Host OpenAPI 路径 | 89 |
| Edge OpenAPI 路径 | 13 |
| Host 路由装饰器（静态扫描） | 85 |
| Edge 路由装饰器（静态扫描） | 13 |

Host 关键路由组：`/v1/chat` `/v1/host/input` `/v1/edge/events` `/v1/agent/*` `/tools/*` `/bond/*` `/scheduler/*` `/host-scheduler/*` `/obs/*`
Edge 关键路由组：`/v1/edge/health` `/v1/edge/speak` `/v1/edge/vts` `/v1/edge/screenshot` `/v1/edge/automate` `/v1/edge/proactive_*` `/v1/edge/fallback/*`

### 1.3 Skills 面

| 项 | 结果 |
|---|---|
| 运行时注册 Skills 数 | 57 |
| 注册来源 | builtin/file/shell/notification/ide/browser/personality/desktop/memory/emotion/scheduler/system/web + MCP bridge |

### 1.4 测试分层

| 分层 | 用例文件数（`test_*.py`） |
|---|---:|
| `tests/unit` | 202 |
| `tests/contract` | 109 |
| `tests/integration` | 14 |
| `tests/e2e` | 8 |

## 2. 当前运行基线（探测结果）

### 2.1 Host

| 端点 | 结果 |
|---|---|
| `/health` | 200 OK |
| `/ready` | 200 READY |
| `/tools/health` | 200 DEGRADED（catalog_size=0） |
| `/bond/health` | 200 DEGRADED |
| `/scheduler/health` | 503（SCHEDULER_NOT_INITIALIZED） |
| `/host-scheduler/health` | 200（running=false） |

### 2.2 Edge（192.168.1.101:17800）

| 项 | 结果 |
|---|---|
| `/v1/edge/health` | 200，status=ok |
| 核心服务 | gateway/asr/tts/barge_in 可用 |
| fallback llm | 未启用（available=false） |
| VTS 健康状态 | AUTHENTICATED |

## 3. 正式封测目标

1. 全模块入口可达，无孤岛模块。
2. Host/Edge 全链路节点连通，无参数断档。
3. 57 个 skills 全量执行路径覆盖（handler + executor）。
4. 语音链路具备可追溯证据（输入 -> chat -> tts -> vts）。
5. 七路召回、情感注入、VTS 表情/口型在同链路内联动可证。
6. **多智能体编排**：复杂场景（AUTO/代码/翻译/执行意图）由 **多 Agent** 执行——execution_profile 选图、Supervisor 分派、专家 Agent（code/translate/retrieval）+ Judge 收口、ReAct/观测/失败收口可追溯；封测覆盖三图主链与多 Agent 专项（见 §4 多智能体矩阵）。
7. WebUI（监控面板）入口、静态资源、数据 API、页面路由和关键交互可用。
8. 形成可复跑、可判定上线门禁的报告产物。

## 4. 正式封测矩阵（待确认）

| ID | 范围 | 方法（无 mock） | 通过标准 | 等级 |
|---|---|---|---|---|
| P0-01 | 仓库扫描 | 真实扫描模块/路由/skills/tests | 清单完整 | 阻断 |
| P0-02 | 语法完整性 | `compileall` Host+Edge | 0 语法错误 | 阻断 |
| P1-01 | Host 核心 | `/health` `/ready` | 均通过 | 阻断 |
| P1-02 | Host 子系统 | tools/bond/scheduler/host-scheduler | 达到上线目标态 | 阻断 |
| P2-01 | Edge 核心 | `/v1/edge/health` | status=ok | 阻断 |
| P2-02 | VTS 可用性 | VTS 进程+8001+健康状态 | 已认证可控 | 阻断 |
| P3-01 | Edge RPC VTS | set_expression/talk_start/talk_end/reset | 全通过 | 阻断 |
| P3-02 | Edge RPC TTS | `/v1/edge/speak` | 有播放启动证据 | 阻断 |
| P3-03 | Edge RPC 截图 | `/v1/edge/screenshot` | image_b64 + 尺寸有效 | 阻断 |
| P3-04 | Edge RPC 自动化 | `/v1/edge/automate` | 动作成功 | 阻断 |
| P3-05 | 主动推送 | proactive_push + proactive_messages | 推送拉取一致 | 阻断 |
| P4-01 | `/v1/host/input` | ASR 事件上报 | 接受且有审计证据 | 阻断 |
| P4-02 | `/v1/edge/events` | ASR_CHUNK/ASR_FINAL | accepted=total | 阻断 |
| P4-03 | `/v1/chat` 主链路 | SCENE_CHAT SSE | COMMIT=APPROVE 且含审计引用 | 阻断 |
| P4-04 | 七路召回 | 同链路核验 | route_count>=7 | 阻断 |
| P4-05 | 情感+VTS | 同链路核验 | emotion+vts证据完整 | 阻断 |
| P4-06 | 场景矩阵（多 Agent） | CHAT/WORK_CODE/WORK_TRANSLATE/EXEC_INTENT/VISION_UI(同意/不同意)；各场景对应 execution_profile 选图，Supervisor + 专家 Agent + Judge 链路可解释提交 | 全场景可解释提交 | 阻断 |
| **MA-01** | **多 Agent：execution_profile 选图** | 入口层写入 execution_profile，按 profile 加载 chat_graph / supervised_graph / intent_graph | 选图正确、无混图 | 阻断 |
| **MA-02** | **多 Agent：Supervised 图主链** | supervisor_dispatch → agent_dispatch → judge_agent → renderer；rule/llm 双模、active_agent_id 分派 | 主链可达、决策与分派可追溯 | 阻断 |
| **MA-03** | **多 Agent：专家 Agent 执行** | code_agent / translate_agent / retrieval_agent 在 Supervised 图内可被分派并执行；ReAct 模式（若启用）有 step/trace | handler 可执行、artifact/trace 可查 | 阻断 |
| **MA-04** | **多 Agent：Judge 收口** | judge_agent 对多工件裁判、输出统一；失败/降级按失败矩阵收口 | 裁判结果稳定、失败码与 trace 一致 | 阻断 |
| **MA-05** | **多 Agent：Intent 图与子图** | INTENT_EXEC 走 intent_graph；intent_agent_subgraph 局部循环/契约（subgraph_trace、失败码） | 图内收口、不跨图逃逸 | 阻断 |
| **MA-06** | **多 Agent：观测与审计** | /obs/metrics、ledger 聚合、subgraph_trace、budget_breach、失败矩阵可查 | 观测面完整、request_id 可追溯 | 阻断 |
| W-01 | WebUI 入口与静态资源 | `/panel` + `/static/monitor/index.html` + 关键 assets | 可访问且资源可加载 | 阻断 |
| W-02 | WebUI API 合约（链路面板） | `/api/ui/timeline` `/api/ui/status-distribution` `/api/ui/request/{id}` `/api/ui/link-topology` `/api/ui/search-requests` | 状态码与结构满足约定 | 阻断 |
| W-03 | WebUI API 合约（观测面板） | `monitor_ui/src/api/obs.js` 引用的 `/obs/*` `/v1/agent/*` | 页面依赖端点可用，无 5xx | 阻断 |
| W-04 | WebUI 前端路由烟测 | `#/overview/sessions/trace/metrics/insights/canon/bond/tools/automation/agent/wechat` | 页面可打开，无白屏/致命 JS 错误 | 阻断 |
| W-05 | WebUI Agent 兼容页面 | 从页面触发 `/v1/agent/run` 并解析 SSE | 只能看到 `AGENT_INIT → LOOP_DONE / REJECTED`，执行轮次和副作用为 0；正向视觉自动化另由受治理 TaskGraph/Edge 路径验收 | 阻断 |
| W-06 | WebUI 打包一致性 | `monitor_ui` 执行 `npm run build` 并对比产物路径 | 构建成功，产物落入 `app/static/monitor` | 非阻断 |
| S-01 | Host 治理与防重放 | Governor + Intent + replay_guard + token gate | 重放/过期/越权被拒且 reason_code 稳定 | 阻断 |
| S-02 | Edge RPC 鉴权与重放 | HMAC 签名、ts 窗口、nonce 唯一性 | 伪签名/超时/重放全部拒绝，合法请求通过 | 阻断 |
| S-03 | 速率与配额 | rate-limit / quota / scheduler 权限边界 | 超限按设计拒绝且无旁路 | 阻断 |
| C-01 | 配置一致性 | Host/Edge `.env` 关键键对齐（RPC host/port/key/auth） | 关键键无冲突、无遗失、无隐式默认误用 | 阻断 |
| E-01 | 外部依赖就绪 | Redis/Neo4j/Chroma/模型运行时联通 | 依赖可达且关键能力可用 | 阻断 |
| O-01 | 可观测性端点完整性 | `/obs/*` `/metrics` `/observability/trace` | 关键端点可读、结构稳定、无 5xx | 阻断 |
| O-02 | Trace 审计闭环 | request_id 追踪 intent/governor/retrieval/voice/avatar | 链路证据可追溯且时序自洽 | 阻断 |
| D-01 | 记忆生命周期 | episodic 写入、distillation、memory guard | 写入与蒸馏规则符合预期，无越界污染 | 阻断 |
| D-02 | Canon 生命周期 | proposal/approve/reject/rollback/vector sync | 变更可审计、可回退、向量同步一致 | 阻断 |
| D-03 | 数据保留与清理 | retention/vault gc/audit retention | 保留策略与清理策略可执行且可证 | 阻断 |
| F-01 | 失败语义矩阵 | failure_matrix + reason_code contract | 失败可归类、可观测、可回归 | 阻断 |
| R-01 | 发布回滚演练 | rollback gate/runbook 路径演练 | 指定失败场景可在目标时限回退 | 阻断 |
| P5-01 | Skills handler | 57 skills 全调用 | 0 异常 | 阻断 |
| P5-02 | Skills executor | 分发执行路径 | 0 异常 + 0 未映射 tool | 阻断 |
| P6-01 | 审计落库 | 多表追踪 request_id | 证据闭环 | 阻断 |
| P7-01 | 上线门禁汇总 | 汇总报告 | 阻断项=0 | 阻断 |

## 5. 正式执行顺序

1. P0 静态扫描与语法检查。
2. P1/P2 双端健康与合约。
3. P3 Edge RPC 功能面联测。
4. P4 主链路与场景矩阵；**MA-01～MA-06 多智能体专项**（与 P4-06 场景矩阵同步或紧接执行）。
5. W WebUI 入口/API/路由/交互专项。
6. S/C/E/O/D/F/R 安全、配置、依赖、观测、数据、失败、回滚专项。
7. P5 Skills 全调用。
8. P6 审计闭环核验。
9. P7 发布结论输出。

## 6. 预期产物

1. `runtime/reports/release_seal_fullstack_*.json`（机器可读）
2. `runtime/reports/release_seal_fullstack_*.md`（人类可读摘要）
3. `docs/50_release/release_seal_execution_report_YYYYMMDD.md`（正式封测结论）

## 7. 已确认

1. `DEGRADED` 统一视为阻断
2. `edge.rpc.fallback_llm=false` 可带条件放行
3. 旧 Agent Loop 已退役，`/v1/agent/run` 必须固定 `REJECTED`；任何正向视觉自动化必须经新的 immutable TaskGraph revision 验收，不能复用本文件旧口径放行
4. `scheduler 未初始化` 与 `tools catalog_size=0` 强阻断
5. WebUI 路由烟测要求必须“真实浏览器”执行（建议：必须，避免仅接口通过但页面白屏）。
6. 回滚演练（R-01）纳入本次上线前“必做阻断项”。
7. 报告必须包含“失败项修复建议 + 回归命令”。
8. **多智能体专项** MA-01～MA-06 为阻断项；复杂场景（P4-06）按多 Agent 编排验收，与 REFACTOR P1～P16 封板基线一致。

## 8. WebUI 现有覆盖与缺口

### 8.1 仓库内已有覆盖（已扫描）

1. 合约测试：`tests/contract/test_web_ui_contract.py`（`/panel`、`/api/ui/*`、只读约束等）。
2. 集成测试：`tests/integration/test_host_integration.py` 包含 `/panel` 重定向检查。
3. 前端代码面：`host_brain/app/monitor_ui/src/App.jsx` 定义 11 个页面路由。
4. 静态发布面：`host_brain/app/static/monitor/*` 已存在构建产物。

### 8.2 当前缺口（本次正式封测会补）

1. 缺少“真实浏览器级”页面烟测（接口通但页面白屏的问题不会被纯后端测试发现）。
2. 缺少“前端页面 -> 后端 API”逐页依赖映射后的全链路判定。
3. 缺少 WebUI Agent 页面触发真实 SSE 的端到端操作证据（截图/日志/报告字段）。

### 8.3 WebUI 专项实测输出（计划）

1. 页面路由可达矩阵（每页 `PASS/FAIL` + 错误摘要）。
2. 网络请求矩阵（每页触发 API 列表、状态码、失败率）。
3. 控制台错误矩阵（致命 JS 错误、资源 404、跨域错误）。
4. WebUI 最终闸门判定（阻断项与放行项分离）。

## 9. 文档扫描补充维度（代码优先）

说明：本节来自对 `docs/` 全量扫描（89 个 Markdown）的补充提炼。
执行原则：文档仅提供“关注点”，最终以当前代码实现与运行结果为准。

扫描分布：`60_trace=67`、`15_theory=6`、`50_project=7`、`30_devspec=3`、`20_prd=1`、`10_whitepaper=1`、`40_agents=2`、`00_index=1`、`50_release=1`。

### 9.1 本次重点参考文档（高信号）

1. `docs/30_devspec/DevTasks.md`
2. `docs/30_devspec/DevSpec.md`
3. `docs/20_prd/PRD.md`
4. `docs/15_theory/architecture_overview_20260310.md`
5. `docs/60_trace/dataflow_report_20260311.md`
6. `docs/60_trace/requirements_trace.md`
7. `docs/60_trace/risk_registry.md`
8. `docs/50_release/rollback/ROLLBACK_RUNBOOK_2026-03-10.md`
9. `docs/50_release/rollback/RELEASE_ROLLBACK_GATE_20260310_010648.md`
10. `docs/50_project/arch/DEPLOYMENT_ARCHITECTURE.md`
11. `docs/50_project/MULTI_AGENT_ARCH_REFACTOR_2026-03.md`（多智能体架构定版，P0～P16 实施顺序与最终封板）。
12. `docs/50_project/MULTI_AGENT_UPGRADE_PLAN_2026-03-16.md`（多智能体能力与契约参考，M-MA1～M-MA6）。
13. `docs/60_trace/dataflow_report_20260311.md`（数据流全景，含 P15/P16 多智能体分派数据流 §十三）。

### 9.2 文档关注点 -> 代码测试锚点映射

| 文档关注点 | 代码锚点（示例） | 对应矩阵项 |
|---|---|---|
| HMAC/nonce/replay 防重放 | `edge_client/rpc/auth.py` `host_brain/channels/edge_rpc_client.py` | S-02 |
| Governor 分级治理与票据 | `host_brain/governor/*` `host_brain/skills/loader.py` | S-01 |
| 速率限制与配额 | `host_brain/governor/rate_limit_checker.py`（及相关路由） | S-03 |
| 七路召回/ContextPack/StateArbiter | `host_brain/retrieval/*` `host_brain/app/chat_stream_handler.py` | P4-04, O-02 |
| 情感层与 VTS 口型控制 | `host_brain/channels/vts.py` `edge_client/actuators/vts_driver.py` | P4-05, P3-01 |
| WebUI 全链路 | `host_brain/app/web_ui.py` `host_brain/app/monitor_ui/src/*` | W-01~W-06 |
| 审计与可观测性 | `host_brain/audit/*` `host_brain/app/obs_routes.py` | O-01, O-02, P6-01 |
| 记忆蒸馏/遗忘/保留策略 | `host_brain/app/memory/*` | D-01, D-03 |
| Canon 演化与回滚 | `host_brain/app/canon_routes.py` `host_brain/*canon*` | D-02, R-01 |
| 发布回滚门禁 | `docs/60_trace/*rollback*` + `tests/*rollback*` | R-01 |
| 多智能体编排与 Governed Runtime（P1～P16 封板） | `host_brain/app/entry_routing.py` `host_brain/app/execution_profile.py` `host_brain/app/handlers/supervisor_dispatch_handler.py` `host_brain/integrations/graph_executor.py` `host_brain/app/handlers/*_agent_handler.py` `host_brain/app/agents/react_loop.py` `host_brain/observability/metrics.py` `host_brain/audit/sqlite_ledger.py` | P4-06（场景矩阵）、**MA-01～MA-06**（多 Agent 专项）、O-02（handoff/trace） |

### 9.3 维度扩充结论

1. 当前计划已从“功能面”扩充到“治理、安全、可观测、生命周期、回滚”全域。
2. 正式实测时将按新增矩阵项执行，不只做接口通断。
3. 所有文档派生项都会落到“可执行测试 + 证据文件”两层，不做口头通过。

## 10. 多智能体架构升级与封测

**多智能体重构升级已完成最终封板（P1～P16）**，封测计划已将多智能体纳入正式矩阵：

- **架构与阶段依据**：
  - `docs/50_project/MULTI_AGENT_ARCH_REFACTOR_2026-03.md`（定版方案，P0～P16 实施顺序与基线说明）
  - `docs/50_project/MULTI_AGENT_UPGRADE_PLAN_2026-03-16.md`（能力与契约参考）
  - P1～P16 各轮 SCOPE/SEAL_RESULT（范围与验收结论）
- **封测中的多智能体覆盖**：
  - **P4-06** 场景矩阵：CHAT / WORK_CODE / WORK_TRANSLATE / EXEC_INTENT / VISION_UI 等场景均按 **多 Agent** 编排（execution_profile 选图、Supervisor + 专家 Agent + Judge）；“复杂场景”即多 Agent 场景，验收标准为全场景可解释提交。
  - **MA-01～MA-06** 多智能体专项：execution_profile 选图、Supervised 图主链、专家 Agent 执行与 Judge 收口、Intent 图与子图、观测与审计；与 P4-06 同步或紧接执行（见 §4 封测矩阵、§5 执行顺序）。
  - **O-02** 审计闭环：trace 覆盖 intent/governor/retrieval/voice/avatar 及 **handoff/agent 分派**，多 Agent 链路可追溯。
- **通过标准**：封测通过即包含多智能体基线通过；MA-01～MA-06 为阻断项，与 P4-06 一并判定上线门禁。

---
