受众：CODEX
标题：七海实时情感数字生命测试计划

# 测试计划

本文件定义日常稳定测试政策与最低门槛。整个 Natsume AI 2.0 的代码覆盖、十五项架构 Gate、七条生产路线、PlanningSearch 三域收益和最终放行规则见 [系统 2.0 总验收方案](../docs/35_quality/SYSTEM_V2_MASTER_ACCEPTANCE_PLAN.md)；双机环境、逐阶段命令、72 条情感场景、故障矩阵、性能采样和人工盲测的可执行步骤见 [七海数字生命双机全栈详细测试方案](../docs/35_quality/DIGITAL_LIFE_FULL_STACK_TEST_PLAN.md)。

当前全系统 line／branch coverage 仍为 `UNKNOWN`，不是 0%，也不是测试文件数量推算出的百分比。S0 已删除测试对退役 Executive helper 的失效依赖，定向 7 项通过；全仓现可完整收集 11881 个 case、0 error。`.coveragerc` 已将 `host_brain`、`edge_client`、`natsume_contracts` 纳入 branch coverage，Monitor 也提供 V8/LCOV read-model 入口；首次完整报告生成前仍不得声明覆盖率或 Architecture 2.0 验收通过。

首次 Monitor read-model 覆盖采集为 line 61.31%、branch 52.78%、function 53.59%，27 项测试全部通过，但低于系统 2.0 目标 80%/70%。Python 7 项 smoke 将 274596 条生产语句纳入分母并得到 line 7.87%，只证明配置能扫描完整源树，不能作为 Python 全系统覆盖率；完整稳定测试分片正在生成正式 baseline。

Unit 无 coverage 预检已在 `02a24e4b8` 上稳定完成：930 个 Unit 文件按 18 个独立进程分片，固定排除 `e2e/hw/prod_env/runtime_probe/docker/ci_unstable`，独立聚合 18 个 JUnit 得到 8318 tests、8310 passed、8 skipped、0 failed、0 errors。4 项 skipped 是当前 Windows 无符号链接创建权限，4 项是明确退役的 legacy recent-turn direct route；状态记为 `PASS_WITH_8_CLASSIFIED_SKIPS`，不能外推为 Contract／Integration／E2E 或 Architecture 2.0 PASS。JUnit 位于忽略目录 `artifacts/local-gates/02a24e4b8/unit-fine-preflight/`；昂贵 coverage 采集从该稳定测试基线开始，只执行一次。

Unit coverage 初次采集在 `4a232d5fc` 上以相同 18 分片运行，JUnit 因 2 个 subtest 展开为 8320 条：8308 passed、4 failed、8 skipped、0 errors。一个失败是 instrumentation 下 120ms 事件循环阈值稳定超时，而同步 embedding 模拟仅为 200ms；一个是 `node_grants.py` 超既有 1500 行上限 6 行；另两个是 side-effect-free Skill preflight 前移后，`execute_stream` 结构哈希与 MCP guard 所有者静态合同未同步。修复不改生产执行语义：非阻塞测试直接断言 embedding 执行线程与会话事件循环线程不同，删除受 coverage 和主机负载影响的墙钟门槛；压缩 `__all__` 表达而不扩大文件白名单；冻结哈希更新为已审查方法结构；MCP 合同改验真正的 `_prepare_frozen_skill_input` 及其在 dependency/Preview 前的调用顺序。受影响 shard-2／5／6／9 重采样全部通过，合并其余 14 个未变化分片后，JUnit 为 8320 tests、8312 passed、8 classified skips、0 failed／error；Unit 覆盖为 line 168945/276262（61.15%）、branch 39228/90738（43.23%），Coverage.py 综合值 56.72%。证据位于忽略目录 `artifacts/local-gates/e8a288363/python-coverage/unit-composite/`。这只是 Unit baseline，Contract／Integration 尚未纳入同一 coverage 数据集，因此全系统百分比仍为 `UNKNOWN`。

Contract 无 coverage 首轮预检在 `ebd25de95d` 上完成：4 个分片共 1949 项，1947 passed、2 failed、0 skipped／error。两个尾部失败均为确定性合同维护问题：Canon 安全临时文件修复后静态边界策略仍引用旧行号，以及 NE-04 stdlib worker 使用裸 `print` 输出机器回执；两项定向复验 2 passed，Evolution Lab 相邻回归 12 passed。`4724af2f9` 当前冻结重跑仍收集 1949 项，4 个隔离 JUnit 合计 1949 passed、0 failed／skipped／error，因此 Contract 稳定预检现记为 `PASS`。该结论不包含 Unit／Integration／E2E、真实服务、覆盖率或体验验收。

Integration 无 coverage 首轮稳定预检在 `1d68ecb1ce` 上选择 155 个文件，排除 `e2e/prod_env/runtime_probe/real_docker/ci_unstable` 后以 6 个隔离进程执行，共收集 849 项：730 passed、50 failed、67 errors、2 个 Redis 环境 skip。67 个 setup error 来自测试夹具同时继承本机 workspace secret ref 与直接 secret；其余大面积漂移包括测试 lifespan 继承本机 SchedulerClock、migration head 固定在 0123/0125、空 TaskGraph output contract、旧 Bond 读投影和普通 Chat 重型 TaskGraph 假设。第一批修复已用 3 组整文件/定向组合证据关闭其中 90 个红项：Host 原 67 个 setup error 及其后暴露的 9 个退役/实时合同断言、HTTP Host Input 5 项、HTTP Skill 4 项、TaskGraph/migration/OBS 14 项均通过；尚余 27 个运行时、恢复和 Review 语义红项，状态为 `REMEDIATION_IN_PROGRESS`，不是 Integration PASS。

上述 27 项随后在 `a829f97de3` 上按 4 个低负载隔离进程精确复跑：8 项独立通过，确认其首轮失败来自并行负载或进程内顺序污染；19 项稳定复现。第二批先收口其中 7 项旧入口/夹具漂移：Worker 与 Self-heal 自然语言请求必须进入 `GOV_AIRLOCK_REVIEW` 且 dispatch 次数为 0；调用方提供的 `planned_steps`/多 Skill Plan 必须在 admission 前拒绝；Skill 成功以 canonical execution receipt 为真相，不再要求兼容 triptych 账本完成；Self-evolve 直接调用先由 governed-admission 门禁拒绝；对话连续性通过 app-local typed read ports 从真实 SQLite conversation window 注入 2 个 turn。组合定向复验为 7 passed，稳定红项降至 12；全量 Integration 仍待生产恢复问题清零后在冻结 SHA 重跑。

第三批关闭 2 项真实 migration receipt-loss replay 缺陷：当前物理库保留 `0148/0149` schema、但 migration catalog 从 `0105/0106` 起丢失时，runner 过去会在 companion profile 列与 Chat delivery trigger 上重复执行失败。修复只接受精确 frozen checksum，并完整验证目标列、约束、索引和 trigger；任一 trigger 被篡改或缺失均拒绝兼容。两项原集成回归与一项 tamper 单测合计 3 passed，稳定红项降至 10；这仍不是全量 Integration PASS。

第四批关闭 3 项执行边界缺陷或夹具漂移。Skill 的 side-effect-free 依赖与 frozen payload 预检现在发生在 Operation reservation 之前，缺失 Memory backend 会稳定落成 `FAILED / GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE`，不会伪造 Skill receipt，也不会误标 unknown effect；Node grant 的 durable batch 在过期后只允许重开不可变身份以投影终态，实际 claim/worker 仍由 verifier fail-closed，因此 Operator／Scheduler 历史成功不会被当前时钟改判；安全取消集成夹具改走当前 `governed_operator_runtime` 端口，不再注入已退役 service 字段。相邻回归 23 passed，包含五次 exact replay 的完整 Operator 流程另以 149.17 秒通过，稳定红项降至 7；仍须清零并冻结 SHA 全量复跑。

第五批已把剩余 7 项稳定红项定向清零。两项 Internal artifact 用例在同进程复跑时确认属于旧负载／顺序假红；四项 GR-03 DAG 用例改为消费正式 Node Budget Reservation、真实 ReviewStore，并按当前 Attempt／Lease／Node Budget Outbox 合同验证精确重放；最后一项真实缺陷是预算拒绝后的 Internal Agent 单节点 `delegate:agent_runtime` 终态仍被 Workspace 的 Plan 专用校验拒绝。修复后 Workspace abandonment 同时精确绑定 Graph terminal Outbox、Node Budget、Worker Projection 与 Secure Ingress result digest，篡改终态保持 fail-closed。7 项组合复验为 7 passed，当前已知稳定红项为 0；状态仅提升为 `REMEDIATED_PENDING_FROZEN_RERUN`，完整 Integration 尚未在冻结 SHA 重跑，仍不是 Integration PASS。

首次冻结重跑已在 `15cebc540` 上完成 6 个隔离 JUnit：稳定 marker 范围共 856 项，849 passed、3 failed、4 skipped、0 errors。4 项 skip 中 2 项是明确 opt-in 的 real Docker Golden Suite，2 项缺少 `NASTUME_TEST_REDIS_URL`；3 项失败低负载组合复跑为 3 passed。根因均为测试夹具的墙钟假设：Long-term Goal 在 migration 前采样时间，重负载迁移超过 120 秒后才签发已经过期的 runtime lease；Heartbeat-loss 用 120ms TTL，可能在进入待测心跳阶段前自然过期。修复只把 Lease 时间移到 migration 后采样，并复用已稳定的 2s TTL／2.5s worker 比例，不改生产 fencing 或触发器；定向复验 3 passed。`8fbb6a833` 随后只改动这两个受影响夹具及文档，生产代码和其余四片测试文件未变化；shard-0 重跑 176 passed，shard-2 重跑 126 passed。合并未变化四片的原 JUnit 后，当前代码树稳定 Integration 结论为 852 passed、4 classified skips、0 failed／error，状态 `PASS_WITH_4_CLASSIFIED_SKIPS`。该结论仍不包含真实 Docker／Redis、E2E、Security、覆盖率或数字生命体验验收。

`d0a0adb65` 的 Security coverage 两片共 379 项：377 passed、2 项因当前 Windows 无符号链接权限分类跳过、0 failed／error。本地可执行 E2E coverage 首轮 69 项中 68 passed、1 failed；稳定复现确认旧 M48-B OBS Golden 只替换 `_HANDLERS`，而 `sys_mode.switch` 已由 canonical `_ROUTE_PORT_HANDLERS` overlay 接管，录制端口未进入执行并被正确标为 `OBS_COMMAND_EXECUTION_UNCERTAIN`。夹具现同时替换并恢复当前 route-handler test seam，不改生产路由或授权；无 coverage 与 coverage 定向复验各 1 passed，`67842d7b2` 上受影响 shard-1 为 35 passed，合并未变化 shard-0 后本地 E2E 为 69 passed、0 failed／skipped／error。

离线稳定 Python coverage 已在 `67842d7b2` 收口：Unit、Contract、Integration、Security 和本地 E2E 共 11573 条 JUnit，11559 passed、14 classified skips、0 failed／error。Integration coverage 满载首轮仅 32-writer Budget contention 在 `BEGIN IMMEDIATE` 超过 SQLite 5 秒等待窗；低负载普通与 coverage 定向各 1 passed，整片隔离重采 169 passed，最终 Integration 仍为 852 passed、4 classified skips。完整生产源码分母得到 line 199504/276262（72.22%）、branch 49976/90738（55.08%），均低于 75%/65% Gate；高风险合集为 78.49%/61.75%，当前分支变更为 82.30%/66.39%，也低于 90%/85% 与 95%/90% Gate。JSON、XML、HTML、原始分片来源和范围报告位于忽略目录 `artifacts/local-gates/67842d7b2/python-coverage/stable-all/`。真实 Docker、Provider、远端 Edge／硬件和 runtime probe 尚未加入，因此这是一份可复核的离线 baseline，不是最终 Full Architecture PASS。

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

冻结覆盖定义为 `tests/fixtures/emotional_companion/dl00_multiturn_baseline_v1.json`，真实执行映射为 `tests/fixtures/emotional_companion/dl07_live_execution_v1.json`。前者的 `context_turns` 允许描述测试前提，禁止原样作为用户台词；后者只为 `gateway_text` 家族提供自然 setup。真实校准示例：

```powershell
python scripts/runtime/test_digital_life_live_cohort.py `
  --case implicit_low_mood:1 `
  --case user_angry_at_natsume:1 `
  --db-path <acceptance-audit.sqlite3> `
  --report-dir <acceptance-evidence-dir> `
  --require-voice
```

脚本始终以 `conversation_id + msg_id` 绑定每轮终态。多模态、主动、跨重启或 Edge 故障场景缺少真实驱动时必须为 `NOT_RUN_REQUIRED_DRIVER`；自动结构通过仍固定为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`，不能替代盲测。

报告 schema v2 还必须在 cohort 前后读取八张退役 affect writer 表、`~/.natsume/emotion/*.json` 的脱敏指纹，以及 `affective_life_event_v1`、`relationship_life_event_v1` 等五张 canonical 表。存在 accepted Gateway turn 时，任何旧表增量、旧文件新增／删除／变更、缺失 inventory 或 canonical Affective／Relationship 不前进都令 probe `FAIL`；没有 accepted turn 只能是 `NOT_ELIGIBLE`，不能用失败请求证明 writer 清零。

当前正式样本绑定 `4f4008060`：3 个 accepted turn，八张退役表全为 `0→0`，4 个旧文件零变化；五张 canonical 表分别 +8／+6／+22／+3／+12。该收据关闭旧 writer 运行使用量，不替代 48 条盲测、profile 物理行为或长期 soak。

Gateway Webhook 必须先得到 durable inbox 的 `accepted/duplicate` 回执，才允许轮询 Host 终态。签名漂移、401/403、未注册 adapter 或网络错误属于入口终态失败，runner 必须立即报告实际 HTTP 原因，禁止继续消耗整轮 180 秒等待窗口。双机启动后还要以不暴露原文的长度与 SHA-256 摘要核对 Host/Edge 回调 HMAC，并在变更密钥后重启 Gateway 使新进程接线生效。

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

Host pre-Operation 崩溃必须用真实模型调用注入：在 Gateway 已接受、TaskNode 已进入 `RUNNING` 后终止 Host，等待旧 Lease 到期并由 survivor 取得更高 fencing token。Node Grant 的到期时间必须晚于“冻结时间预算 + 一个恢复扫描窗口”；最终断言只有一个 HostCommand、TaskGraph、Attempt 业务身份和外部 delivery intent。文件型跨进程测试用于回归，但不能替代这条真实进程故障证据。周期恢复故障日志只能出现稳定 reason code／异常类型，同一故障恢复前不得每 5 秒刷屏，也不得写入用户正文。

当前 A15 真实复测为 `PASS_REAL_HOST_CRASH_RECOVERY`：fencing token 1 的 Lease 到期后 2.446 秒由 token 2 接管同一个 Attempt，接管时 Node Grant 尚余 57.211 秒；最终只有 1 个 HostCommand、1 张 TaskGraph、1 个 Attempt、1 次 Acceptance，Budget 为 `CONSUMED`，6 类 Acceptance Effect 各提交一次。脱敏报告 SHA-256 为 `e9f038a5e735ec29f935c57382df5fec48d692f593c8a31450dcbbbd6209fdb7`。微信客户端不可用导致物理外发进入 DLQ，仍按 `NOT_TESTED_CLIENT_UNAVAILABLE` 处理，不能由本项 PASS 覆盖。

Terminal reconciliation canary 必须从本轮 MigrationResult 的 `current_version == latest_version` 推导实际 catalog head，禁止以历史常量兜底。当前隔离真实 SQLite 复跑覆盖一个 `EXACT_REPAIRABLE` 和一个 `SIGNED_REPAIR_REQUIRED` 半终态：前者只能按 durable evidence 自动释放预算，后者必须保持未修改并阻断 production-ready；两者均不得产生外部副作用。

真实 Gateway live matrix 必须使用生产 HMAC 边界，密钥只从运行时 secret env 读取且不得写入报告。图证据以 `host_command_journal` → `task_runtime_graph` → `task_runtime_node` 为权威；`graph_run_trace` 只允许作为历史数据库兼容读取，不能再作为当前通过依据。探针若因 schema 漂移崩溃、Host Credential Gate 拒绝或 Governor 拒绝，均记为 `FAIL` 并保留对应 DLQ／audit 事实。

探针轮询必须把 `INPUT_COMPLETED` 与 `INPUT_FAILED` 都视为终态：失败时立即结束等待并报告 durable `reason_code`／detail；只有 `INPUT_COMPLETED` 可以进入语音成功断言。终态后必须重新读取 Host 日志，避免用 admission 前的旧日志片段漏掉 `dispatch failed`。远端启停脚本必须用命名参数 splatting 调用 `stop_edge.ps1`，并在真实停止／重启演练中验证 PID 文件与监听端口都指向本轮隔离运行根。2026-08-04 最新翻译实测已在入口与 Governor 通过后暴露 `GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE`，同时常驻 ASR 噪声会破坏 Host 启动稳定性；两项均保持 `FAIL／REMEDIATION_REQUIRED`，不得由 Gateway health 或禁用 ASR 后的稳定运行替代。

翻译修复的本地 Gate 必须同时覆盖：V2 target policy 内容寻址、低风险 Review waiver、冻结 Task model guard、canonical prompt identity、真实 usage 结算、无工具／副作用、未知 target fail-closed，以及 Pentest V1 零用量语义不回退。当前定向与相邻回归为 158 passed；仍须重启真实 Host 后用签名 Gateway webhook 取得新的 `INPUT_COMPLETED`、TaskGraph、ModelAttempt、Budget、Acceptance、Gateway 与 Edge receipt，才能把 `CODE_REMEDIATED_REAL_RETEST_PENDING` 提升为通过。

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

## Gateway 当前闭环与 Life Body 投影 Gate

Gateway 当前请求只有在 `INPUT_COMPLETED` 同时携带以下精确枚举时，才计入 Reply Outbox committed：

```text
gateway_delivery_authority = acceptance_effect_outbox
gateway_delivery_state     = OUTBOX_COMMITTED
```

断言必须同时验证：公开投影不含 `reply_preview` 或回复正文；canonical commit 能关闭当前请求的 ACK/progress 假缺口；legacy terminal 行仍可只读统计；360 分钟历史失败不被覆盖；Outbox commit 的 UI 文案明确“不等于外部平台物理 ACK”。不得通过降低阈值或补造已退役事件让指标变绿。

Life Body 必须从真实 `digital_life_runtime_snapshot.hard_state.emotion_tag` 派生 `derived.currentEmotion`，并以真实 OBS／Nginx Monitor 浏览器断言页面不存在“待连接／待恢复”。仅构造 fixture 或 hook 默认值不能算真实通过。

2026-08-04 当前证据：受影响 Python 30 passed、Monitor read-model 27 passed、生产构建通过、真实浏览器两条用例通过；真实签名 Gateway 三条新请求的最新有效桶 Reply Commit／Closed Loop／Completion 为 100%。整体仍按 `INCOMPLETE` 处理。

Core Release manifest 合同必须把当前 migration catalog head 与完整 migration asset group 分开校验。新增 append-only migration 后，head 断言、通用 Runtime 的 `catalog_migration_head` 和机器状态必须在同一提交前进；历史七路线 `accepted_migration_head` 继续绑定原 receipt，禁止借 catalog 前进伪造重验收。当前 catalog head 为 `0153_terminal_prebudget_cancellation_classifier_correction.sql`。

自然文本情感快路径的最小回归为：

```powershell
python -m pytest -q `
  tests/unit/test_dl02_affect_fusion.py `
  tests/integration/test_dl02_affect_fusion_restart.py `
  tests/unit/test_dl04_affective_turn_planner.py `
  tests/unit/test_emotion_context_helpers_extra.py `
  tests/unit/test_surface_runtime.py
```

断言不仅检查情绪标签，还检查 canonical appraisal kind、self/user 分离、`LISTEN`／`RESPECT_AUTONOMY`、候选数量以及模型调用次数。真实验收必须另行执行 `test_digital_life_live_cohort.py`，并人工阅读逐轮回复；结构通过不能覆盖“早点休息”等违背当前用户边界的内容。

多轮 runner 的独立回归还必须断言：同一轨迹所有 webhook 使用同一个 sender，从而得到唯一 `relationship_subject_id`；每轮 `msg_id` 仍唯一；终态 payload 的 `person_id` 和 TaskGraph `session_id` 在全轨迹内各只有一个值。只固定 `conversation_id` 不足以证明关系连续性。

真实稳定主体首轮四轨迹报告 `dl-live-1785961076715.json` 的 SHA-256 为 `c246275def1c2884296df01d792d525fe931cfeae95e39537ec50580af0cedda`：2 条结构通过、2 条失败，但人工内容均未达到放行口径。边界回复出现“嫌我烦”的负罪暗示，现实自主回复出现“别瞎想”，倾听回复出现“以你的性子”的无证据人格判断，吃醋问题被“料理AI”玩笑回避，混合情绪末轮因双候选数量错误拒绝。上述问题必须分别由 canonical CompanionAct 与窄硬门禁复验；第二次模型尝试只有一个完整安全候选时允许降级送达，但 `candidate_count=1`、`benefit_eligible=false`，不得冒充双候选通过。

第二轮真实报告 `dl-live-1785963171706.json` 的 SHA-256 为 `6c5c12f21e39f956af39d3466d5805a871a978864a25aa9864620653844b3871`：3 条自动结构通过、1 条失败，但人工仍没有整体通过。独立 runner verifier 现在逐轮拒绝四个已知体验回归族，并要求深路径 `candidate_count=2`、`selected_passed=true`、`benefit_eligible=true`；否则自动状态必须为 `FAIL`。这套 verifier 与生产 Surface 门禁分开实现，避免用同一判断代码自证正确。

选择器修复后的三条选择性报告 `dl-live-1785964323732.json` 的 SHA-256 为 `2a5028cfa6cf730d20e7d2ac4d0a446c81ece99a837ee06a8bec5a64461124e2`：三条 TaskGraph 和真实 Voice 均完成，吃醋轮成功改选安全候选；自动状态为 2 通过、1 失败，人工全部仍待评。第三轮定向回归新增“省点力气”“懒得管／别瞎操心”“把怕先放一放”以及无事实依据的“当面讲／陪你坐会儿”，同时验证显式见面上下文不会被误拦截；105 项通过。下一步必须加载新 SHA 后真实重跑，不能用这些单元结果替换人感证据。

第三轮真实报告 `dl-live-1785965355637.json` 的 SHA-256 为 `6b1470e8ebb3892d33f3a3eaa34350c34aeda0b4f7d5ac1ae79d1e6132c0f82c`：3/3 自动结构通过，三条末轮 Voice 均 `APPLIED`，人工仍为 `PENDING`。新增人工回归是忙碌式边界反驳、用“哼回去”赶走害怕、吃味后的“不会成为麻烦”；第四轮同时修正上游 CompanionAct 指导和下游候选门禁，110 项定向测试通过。真实新 SHA 重跑前不得将自动 3/3 提升为人感通过。

最终三轨迹报告 `dl-live-1785966063542.json` 的 SHA-256 为 `e004cb49ef1e4d90028f49d8ca87c790e530a7b60200924b93fdd7e34c9b9f5f`：2 自动通过、1 失败，三条 Voice 均 `APPLIED`。自动评分把自然的“有点怕也正常”判低分，却放过“省得你嫌烦”，因此该 scorer 只保留候选排序用途，不得作为人感验收 authority。补齐三个同义退化族后 101 项通过；后续扩大 cohort 并由盲测标签校准，而非继续围绕三条样本调分。

真实 unknown-effect 故障项必须在 operation reservation 为 `RESERVED` 后终止独立 Host 进程，使用同一真实 SQLite 重启并等待 Lease 过期。断言同一 Attempt 为 `RECOVERABLE`、reservation 为 `RECOVERY_REQUIRED`、存在 `UNKNOWN_SIDE_EFFECT / SIGNED_OPERATION_RECONCILIATION_REQUIRED` hold，且观察窗口内 Command／Graph／Attempt／模型调用／Acceptance／Effect／Gateway reply 均不增加。该结果不得用于证明 provider transport 已发出；后者需要独立 transport attestation。

Host 启动参数回归命令：

```powershell
python -m pytest -q tests/unit/test_runtime_startup_script_composition.py
```

除静态断言外，A15 Sidecar 还要用故意冲突的进程环境 `127.0.0.1:19999` 与显式参数 `0.0.0.0:18083` 启动，确认 runtime snapshot、Service Catalog candidate 和实际 listener 都采用显式值。

Task Runtime rollback failure 的独立命令为：

```powershell
python -m pytest -q tests/integration/test_ne01_task_runtime_rollback_failure_canary.py
python scripts/runtime/run_task_runtime_rollback_failure_canary.py `
  --run-id <acceptance-run-id> `
  --receipt <isolated-evidence-path>
```

必须断言激活失败后 rollback close 失败不会把 Container 标成 closed，第二次 activate 以 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED` 阻断，第一次全容器 close 保留 Store，故障解除后的重试关闭成功，终态重复 close 为 no-op。Canary 使用完整迁移后的真实临时 SQLite，但不等价于远端设备或外部 provider 回滚。

Sidecar 停机回归必须先记录主 Obs／Nginx PID，对一个没有 PID 文件的显式空 RuntimeRoot 调用 `stop_host_side.ps1`，再确认主 PID 未变且 `/health`、`/monitor/index.html` 继续返回 200。静态合同同时要求 `host.runtime.env` 在子停止器执行后才删除，显式 RuntimeRoot 的 Obs 禁止全局扫描，Monitor 仅停止命令行匹配目标 Nginx prefix 的监听进程。

Gateway Outbox 网络恢复的独立命令为：

```powershell
python -m pytest -q tests/integration/test_gateway_outbox_network_recovery_acceptance.py
python scripts/runtime/run_gateway_outbox_network_recovery_acceptance.py `
  --run-id <acceptance-run-id> `
  --receipt <isolated-evidence-path>
```

Canary 必须使用完整 migration catalog、真实临时 SQLite、生产 `GatewayDeliveryRuntime`／`OutboundDispatcher`／`QQAdapter` 和真实 loopback HTTP socket。适配器选择失败发生在 `dispatch_started` 前，可以有界重试；OneBot peer 可用后只允许一次 HTTP。若 peer 已读取请求但丢失响应，则 Outbox 必须进入 `GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN`／DLQ，重复提交、进程重启和无 reconciliation 的 operator replay 均不得再次发送。Loopback peer 不是 QQ／微信客户端，其成功响应或本地 DLQ 都不能作为物理平台送达证明。

Digital Life 情感作用域、隐私和有界重放的独立命令为：

```powershell
python -m pytest -q tests/integration/test_digital_life_scope_privacy_acceptance.py
python scripts/runtime/run_digital_life_scope_privacy_acceptance.py `
  --run-id <acceptance-run-id> `
  --event-count 1001 `
  --receipt <isolated-evidence-path>
```

正式收据必须使用完整 migration catalog、真实临时 SQLite、生产 `AffectFusionService`／`LifeCommandService`／`LifeCommandRepository`。四组 scope 需要同时改变 user、workspace 和 session，并确认主 scope 的 1001 条事件不会改变其他 scope digest；raw audio/image/video observation 应在 fusion 被省略，绕过 fusion 的 raw command 也必须被 reducer 拒绝。扫描范围包括 affective command、Life event、canonical snapshot 和 projection intent；不得出现原始正文 sentinel 或禁止字段。最终 snapshot 最多 4 个 active episode、8 个 component、每个 episode 16 个 evidence ref，并在新 Repository 重启重放后得到完全相同 digest。

Companion Profile 的独立 Host 行为验收命令为：

```powershell
python -m pytest -q tests/integration/test_companion_profile_behavior_acceptance.py
python scripts/runtime/run_companion_profile_behavior_acceptance.py `
  --run-id <acceptance-run-id> `
  --receipt <isolated-evidence-path>
```

Canary 必须使用真实临时 SQLite 和生产 Relationship/Profile/Fusion/Planner/Expression Delivery 组件：五条跨至少三天、helpfulness 下界不低于 0.85 且无边界违规的 aftereffect 才能开始；候选必须真实改变下一轮文本生成约束、Voice rate 和 Avatar motion，五次后效应后保持同一候选并自动激活；rollback 必须恢复精确前代的 profile identity、文本策略和两通道参数。该收据只证明 Host 生成与协议接线，不得冒充模型实际措辞或远端 Voice／Avatar 物理执行。

普通 Gateway 陪伴的模型可用性回归必须同时验证：`input_source=GATEWAY` 且没有 canonical accepted predecessor artifact 时，冻结后的 Chat trace 选择 `realtime_companion`，实际 `ModelAttemptRequest` 使用 `CHAT_SURFACE_REALTIME`／Tier B；含前序 artifact 的 Plan 终局综合和深路径候选仍使用质量优先 Tier A。Tier B 的 server-owned target timeout 默认 15 秒；transport 在文本可见前失败时，只能在同一冻结 target 和现有 Task retry budget 内额外尝试一次，不能生成第二张图或重复 delivery。成功 provider response 仍必须带 canonical token usage，缺 usage 的成功响应不得因“陪伴优先”而通过。

`CHAT_SURFACE_REALTIME` 当前冻结为 640 output tokens，并使用服务端 typed `thinking_mode=disabled`。Route boundary 只接受 `enabled/disabled`；DeepSeek provider adapter 必须把它转换为顶层 `thinking={"type":"disabled"}`，OpenAI、Gemini、Minimax 等 provider 必须删除提示而不是收到未知字段。Plan、Research 和高歧义深路径不得继承普通陪伴的 non-thinking 配置。回归仍要拒绝 `finish_reason=length` 且只有 `reasoning_content` 的空正文，不能把 reasoning 当用户可见回复；真实复验同时记录 completion token、content presence、provider latency 与最终 Voice receipt。

`c6a6e0b36` 的三轮真实校准已关闭“默认 thinking 耗尽正文预算”断点：三轮图和 Acceptance 全部完成，最终轮 Voice 物理回执为 `APPLIED`。该报告仍不能关闭实时门槛或人感门槛；整轮 Host latency 为 7,739～12,715 ms，前两轮各需一次 `surface_hard_gate_repair`，必须继续测量首 token、确定性链路和语义修复比例。

HTTP 集成断言还要区分“自包含的小代码片段”和“需要 Workspace 的代码任务”：前者应留在实时 Chat，以 inline-code semantic 直接回答，不应启动 `agent_inspect`；包含文件、仓库、调试、测试或修改语义的任务才进入 Governed Agent Runtime。不得用已经退役的可变 process-service 测试桥注册 Skill 后声称生产 app-local Container 已被覆盖。
