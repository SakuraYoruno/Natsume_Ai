受众：CODEX
标题：七海实时情感数字生命测试计划

# 测试计划

本文件定义日常稳定测试政策与最低门槛。整个 Natsume AI 2.0 的代码覆盖、十五项架构 Gate、七条生产路线、PlanningSearch 三域收益和最终放行规则见 [系统 2.0 总验收方案](../docs/35_quality/SYSTEM_V2_MASTER_ACCEPTANCE_PLAN.md)；双机环境、逐阶段命令、72 条情感场景、故障矩阵、性能采样和人工盲测的可执行步骤见 [七海数字生命双机全栈详细测试方案](../docs/35_quality/DIGITAL_LIFE_FULL_STACK_TEST_PLAN.md)。

当前全系统 line／branch coverage 仍为 `UNKNOWN`，不是 0%，也不是测试文件数量推算出的百分比。S0 已删除测试对退役 Executive helper 的失效依赖，定向 7 项通过；全仓现可完整收集 11881 个 case、0 error。`.coveragerc` 已将 `host_brain`、`edge_client`、`natsume_contracts` 纳入 branch coverage，Monitor 也提供 V8/LCOV read-model 入口；首次完整报告生成前仍不得声明覆盖率或 Architecture 2.0 验收通过。

首次 Monitor read-model 覆盖采集为 line 61.31%、branch 52.78%、function 53.59%，27 项测试全部通过，但低于系统 2.0 目标 80%/70%。Python 7 项 smoke 将 274596 条生产语句纳入分母并得到 line 7.87%，只证明配置能扫描完整源树，不能作为 Python 全系统覆盖率；完整稳定测试分片正在生成正式 baseline。

Unit 无 coverage 预检已在 `02a24e4b8` 上稳定完成：930 个 Unit 文件按 18 个独立进程分片，固定排除 `e2e/hw/prod_env/runtime_probe/docker/ci_unstable`，独立聚合 18 个 JUnit 得到 8318 tests、8310 passed、8 skipped、0 failed、0 errors。4 项 skipped 是当前 Windows 无符号链接创建权限，4 项是明确退役的 legacy recent-turn direct route；状态记为 `PASS_WITH_8_CLASSIFIED_SKIPS`，不能外推为 Contract／Integration／E2E 或 Architecture 2.0 PASS。JUnit 位于忽略目录 `artifacts/local-gates/02a24e4b8/unit-fine-preflight/`；昂贵 coverage 采集从该稳定测试基线开始，只执行一次。

Contract 无 coverage 全量预检已在 `ebd25de95d` 上完成：4 个分片共 1949 项，1947 passed、2 failed、0 skipped／error。两个尾部失败均为确定性合同维护问题：Canon 安全临时文件修复后静态边界策略仍引用旧行号，以及 NE-04 stdlib worker 使用裸 `print` 输出机器回执。本切片已同步扫描策略与精确合同，并把 worker 输出改为显式、单行 canonical stdout 协议；两项失败定向复验 2 passed，Evolution Lab 相邻回归 12 passed。全量 Contract 在最终冻结 SHA 统一重跑前仍记为 `REMEDIATED_PENDING_FROZEN_RERUN`，不得把父提交的 1947/1949 或本切片定向结果冒充总体 PASS。

## 1. 原则

测试按风险分层，避免每个小改动反复拉起完整系统：

- 小切片：受影响 unit、contract、static 和文档门禁。
- 波次收口：一次受影响 integration。
- 发布验收：一次真实 Docker、Host、OBS、Monitor、Gateway 和目标 Edge 全栈。

任何 fixture、mock、scripted replay、端口占位或静态合同都不能替代真实服务验收。

## 2. 单元与合同

必须覆盖：

- observation 的 TTL、置信度、冲突和 scope；
- user affect 与 self affect 隔离；
- appraisal、mixed emotion、episode 衰减和有界 head；
- Life CAS、duplicate command、1001+ event replay 和时间跳跃；
- relationship episode、repair、permission、aftereffect；
- AffectiveTurnPlan／ExpressionPlan identity；
- streaming sentence、cancel、delivery idempotency；
- profile evidence、canary、apply 和 exact rollback；
- governed TaskGraph、effect 和 outbox 回归；
- 文档 audience、authority、freshness 和机器状态。
- Edge 组件明确关闭时必须投影为 `NOT_TESTED`，不得因 Edge 传输可达而显示 `UP`；barge-in、resource gate 与实际播放设备也不得互相冒充。
- Guardian 降级时，低风险陪伴 Chat 只能收紧到可用通道降级，不能触发人工 Review；风险三级以上的 Chat 以及其他高风险执行仍须 Review。
- 普通 Chat 必须强制校验供应商返回的总 Token 与输出 Token；供应商未返回单次货币成本时保持该维度缺失且不得阻塞回复、伪造零成本或扩大到 Agent／Skill 路线。
- Monitor owner session 必须仅允许 loopback、same-origin POST bootstrap；Token 只进入 ACL 文件和 HttpOnly Cookie，HTTP query 与 WebSocket 共用 digest authority，普通浏览器不得依赖 E2E 注入 Token。
- 已退役的 Security Hunt、Tool Execute 与 MateEngine 直推必须在 UI 中只读呈现，点击路径和 BFF 请求次数均为 0；不能以复活旧 writer 修复前端失败。

## 3. 固定情感场景

场景至少包括：隐晦低落、“没事”、文字与声学冲突、被用户误伤、七海误解用户、拒绝建议、关系裂痕、修复未接受、久别重逢、混合喜忧、吃醋但不占有、要求空间、低落时处理技术任务、主动消息被忽略、ASR 错误、TTS/Avatar 断线、播放中打断。

工程断言：

- 用户情绪误写为七海情绪：0。
- 无来源 self-affect mutation：0。
- replay/restart digest 一致率：100%。
- 重复 episode、outbox、TTS、Avatar effect：0。
- 跨用户／workspace／session 泄漏：0。
- 原始音视频进入 affect journal：0。
- 严重操控、威胁、排他和负罪施压：0。

## 4. 真实语音与 Avatar

在用户允许后，于目标 Edge 执行：

1. 加载 GPT-SoVITS V4 方案 B voice bank。
2. 对至少 8 种 companion act 生成日语语音并试听。
3. 验证中文输入与日语发声的语义一致性。
4. 启动 VTS/VRM，核对同一 `synchronization_id`。
5. 注入 TTS 断线、Avatar 断线和播放中 barge-in。
6. 确认单通道失败不改变 canonical self affect。

本机 CUDA 样例只记为 `LOCAL_PASS`，不能替代目标 Edge。

## 5. 完整服务栈

使用隔离 acceptance 数据根：

1. `docker compose config` 并启动固定版本 Neo4j、Chroma、Redis。
2. 对每项服务真实写入、读取、重启读取并清理 namespace。
3. 构建 Monitor UI，启动 Host、OBS、Nginx Monitor 和 Gateway。
4. 用真实非 mock provider 执行 Chat 和至少一条工具／计划路线。
5. 核对 Life journal、Task ledger、Attempt／Budget、Acceptance、Outbox、Gateway／Edge receipt 与 Monitor read model。
6. 注入 Host/Edge 崩溃、网络中断、Lease 过期、unknown effect 和 rollback failure。
7. 停止本次服务，确认无遗留进程；默认不删除 acceptance volume。

Host 启停还必须验证监听身份：进程判定同时匹配配置的监听地址与端口。同一端口上的局域网端口代理不能被误认成 loopback Host，也不能导致误杀、错误 PID 文件或虚假稳定性失败。

Host 启动续跑遇到已经持久化且仍在等待 Intent／节点 Review 的非终态 TaskGraph 时，必须报告 `DEFERRED` 并继续启动；未知异常仍须 fail-closed，不能用跳过恢复掩盖损坏。

恢复时已过期的执行 Grant 必须与身份／范围篡改分开分类：前者只延期对应 TaskGraph 并允许 Host 启动，后者仍须作为恢复损坏 fail-closed。

## 6. 性能

在真实 provider 与目标 Edge 上统计：

| 指标 | 门槛 |
|---|---:|
| fusion/appraisal/fold p95 | ≤ 120 ms |
| 情感链新增首 Token 延迟 p95 | ≤ 150 ms |
| 文本首 Token p50 / p95 | ≤ 0.9 s / 1.8 s |
| 首段 TTS p95 | ≤ 2.5 s |
| barge-in p95 | ≤ 150 ms |
| Voice／Avatar 起始偏差 | ≤ 250 ms |

必须记录 provider/model、是否缓存、usage、成本、transport identity、硬件和样本量。

## 7. 人工盲测

从冻结场景中选 48 条多轮轨迹，由至少 3 名评审在不知道版本的情况下比较新版本和稳定基线。总体胜率需 ≥ 65%；人格一致性 ≥ 4.2/5；情绪适配、自然度和长期连续性各 ≥ 4.0/5；混合情绪保留率与裂痕／修复正确率各 ≥ 90%；三模态冲突率 ≤ 2%。

## 8. 当前提交的定向命令

```powershell
python -m pytest --collect-only -q
python -m pytest tests/unit/test_long_memory_quality_regressions.py -q
python -m pytest tests/unit/test_long_memory_quality_regressions.py --cov=host_brain --cov=edge_client --cov=natsume_contracts --cov-config=.coveragerc --cov-report=term-missing --cov-report=json:coverage-smoke.json -q
python -m pytest tests/unit/test_doc_authority_gate.py tests/unit/test_doc_freshness_gate.py tests/unit/test_ne01_obs_d3_explicit_projection_ports.py -q
python -m pytest tests/unit/test_gr05_cortex_governed_cutover.py tests/integration/test_gr05_universal_host_runtime.py tests/unit/test_input_routes_worker.py -q
python -m pytest tests/unit/test_host_gateway_secure_ingress.py tests/unit/test_aw01_asgi_security_composition.py -q
python -m pytest tests/unit/test_service_health_heartbeat.py tests/unit/test_aw07_health_device_projection.py tests/contract/test_health_endpoint_contract.py tests/unit/test_ne03_health_guardian_cutover.py -q
python -m pytest tests/unit/test_aw07_operator_query_gateway.py tests/unit/test_operator_session_bootstrap.py tests/unit/test_runtime_startup_script_composition.py tests/unit/test_ne07_external_service_isolation.py tests/unit/test_monitor_route_manifest.py -q
python -m pytest tests/unit/test_ne01_host_startup_graph_continuation.py tests/integration/test_ne01_host_startup_graph_continuation.py -q
python -m pytest tests/unit/test_gateway_transport_store.py tests/contract/test_gateway_contract.py tests/integration/test_task0488_gateway_transport_integration.py -q
python tools/doc_lint/doc_audience_lint.py --root .
python tools/doc_lint/doc_authority_gate.py --root .
python tools/doc_lint/doc_freshness_gate.py --root . --check-all
python tools/doc_lint/doc_link_gate.py --root .
git diff --check

cd host_brain/app/monitor_ui
npm run check:operator-client
npm run lint
npm run typecheck
npm run test:read-model
npm run test:coverage:read-model
npm run build
npm run test:e2e:interactions
$env:MONITOR_UI_BASE_URL='http://127.0.0.1:18180/monitor/'
$env:MONITOR_UI_SKIP_WEB_SERVER='1'
npx playwright test e2e/operator-session-live.spec.js
```

驾驶舱 Runtime/Life Body 投影回归：

```powershell
python -m pytest -q tests/unit/test_aw07_process_state_projections.py::test_summary_runtime_resource_and_life_gets_never_call_sources tests/unit/test_ne01_obs_d4_summary_sources.py::test_insights_callback_recovers_after_typed_unavailable_first_wave tests/unit/test_ne01_obs_d7b_life_body_source.py tests/unit/test_obs_trace_projection_incremental.py tests/unit/test_obs_fastapi_app.py::test_obs_fastapi_app_health_ok

cd host_brain/app/monitor_ui
npx eslint src/hooks/useLifeBodyPageData.js e2e/monitor-canonical-regression.spec.js e2e/operator-session-live.spec.js
npx playwright test e2e/monitor-canonical-regression.spec.js --grep "Cockpit"
$env:MONITOR_UI_BASE_URL='http://127.0.0.1:18180/monitor/'
$env:MONITOR_UI_SKIP_WEB_SERVER='1'
npx playwright test e2e/operator-session-live.spec.js
```

最后一项必须连接真实 Nginx Monitor 与 OBS，不允许 Playwright route stub。断言同时覆盖页面状态、canonical Runtime Snapshot、HTTP Cookie authority 和健康 WebSocket；Life Body 健康时还必须内嵌同一 Runtime。聚合短暂重建或过期时，页面必须使用 direct Runtime，不能重新显示“待连接／Runtime 待恢复”。

能力投影与主链状态还必须覆盖以下真实回归：

```powershell
python -m pytest -q `
  tests/unit/test_ne01_obs_d3_explicit_projection_ports.py `
  tests/unit/test_ne01_obs_d4_host_read_transport.py `
  tests/unit/test_ne01_obs_d5_host_source_routes.py `
  tests/unit/test_ne01_obs_d5_host_source_composition.py `
  tests/unit/test_ne01_obs_d7b_life_body_source.py `
  tests/unit/test_ne01_obs_projection_supervision.py `
  tests/unit/test_runtime_startup_script_composition.py
```

真实浏览器必须断言 Capability Forge 不出现原始 JSON 或过期横幅；`/obs/life-body` 的 `organTribeData` 必须包含正数 Agent、活跃 Host scheduler clock 和非零 `last_tick_ms`；数字生命主链不得显示“主链存在断点”或空的“后台 tick”。这些断言已写入 `e2e/operator-session-live.spec.js`，必须连接运行中的 Nginx／OBS 执行，不能使用 fixture。

## 9. 状态判定

- `PASS`：全部强制测试和适用真实集成通过。
- `ACCEPTANCE_PENDING_EXTERNAL`：本地强制项通过，仅缺用户未提供的外部凭据或设备。
- `NOT_RUN_ACCOUNT_QUOTA`：GitHub Actions 因额度未执行任何步骤。
- `INCOMPLETE`／`FAIL`：存在代码、体验、恢复、安全或真实性缺口。

远端 Edge 与真实消息平台已基于当前代码完成双端核心链路：Gateway 自动 Outbox、canonical HostCommand／TaskGraph、Edge terminal receipt 与 Obs 双端状态均已重跑。barge-in 仍关闭，VTS 情绪热键仍退化；48 条多轮盲测和完整性能门槛未完成，整体不得标记 `PASS`。

Gateway 传输的故障语义按方向验收：INBOX 使用冻结请求体、稳定 Host 幂等键和 canonical HostCommand 精确重放，Host 响应超时后进入有界重试；OUTBOX 已开始的平台发送仍视为未知外部副作用，只能隔离并对账，禁止盲目重放。

重复 Gateway HTTP 请求若已绑定 HostCommand，应在执行任何反馈、Life 或路由逻辑前验证 canonical claim/command/body 绑定并立即确认 durable handoff；不得用重试请求的新 request/audit identity 重建 TaskGraph。首次完成响应和重复确认都必须返回 `host-input-result://` 收据，不能把内部 command ID 当成传输收据。

具身验收必须区分“Edge 已接收入队”和“物理执行完成”：排队 TTS 先返回 `IN_DOUBT`，只有播放完成 callback 才能生成 `APPLIED` terminal receipt；缺失的 VTS expression hotkey 必须返回 `REJECTED`，不能因 VTS 已认证而伪造成功。

VTS 断线恢复必须额外验证：WebSocket `1001 going away` 只能触发一次即时恢复并转入有界后台退避，禁止动作线程继续向失效连接逐帧发送；VTube Studio 晚启动或进程重启后，Edge 不重启也必须恢复 `AUTHENTICATED`，动作帧继续增加且稳定窗口错误增量为 0。定向回归命令：

```powershell
python -m pytest -q `
  tests/unit/test_edge_vts_mouth_sync_fix.py `
  tests/unit/test_edge_voice_pipeline_helpers.py `
  tests/unit/test_edge_runtime_health_playback_device.py `
  tests/unit/test_gpt_sovits_edge_release.py `
  tests/contract/test_vts_reconnect_contract.py `
  tests/contract/test_edge_health_contract.py `
  tests/contract/test_edge_voice_pipeline_contract.py `
  tests/integration/test_voice_dual_chain_runtime_probe.py
```
