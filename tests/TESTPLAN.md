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

脚本始终以 `conversation_id + msg_id` 绑定每轮终态。同一轨迹的 Gateway sender／relationship subject 固定不变，但 sender 还绑定本次 `run_id`，防止预检或历史 cohort 的关系状态污染新一轮盲测。多模态、主动、跨重启或 Edge 故障场景缺少真实驱动时必须为 `NOT_RUN_REQUIRED_DRIVER`；自动结构通过仍固定为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`，不能替代盲测。

文本体验队列与物理表达队列分开计时。未指定 `--require-voice` 时，runner 不等待 Edge 空闲，末轮只即时重开一次 Expression receipt；指定 `--require-voice` 时才默认等待 90 秒物理 Voice/Avatar 终态，也可用 `--expression-wait-s` 显式调整。两种模式都必须从 Host canonical Acceptance Effect 独立读到 `conversation.user_turn` 与 `surface.reply_delivery` 已提交，并取得 `gateway-outbox://` receipt；这只证明真实 Gateway durable outbox intake，不证明微信、QQ 或 Telegram 客户端已经外发。外部平台状态固定另记为 `NOT_TESTED_BY_THIS_RUNNER`。

报告逐轮保存 Gateway HTTP、Gateway→Host terminal、TaskGraph、模型 attempt、Surface commit 和 user-turn→Surface commit 的独立耗时。不得把 Expression 后处理轮询时间并入“首个文本可见”延迟，也不得用 TaskGraph `COMPLETED` 代替真实回复投递回执。

由于 realtime Chat 在可见回复后异步提交 Life／Relationship projection，runner 在整个 cohort 结束后最多等待 30 秒 canonical writer 收敛，并把 `background_convergence.waited_ms` 单列；该等待不得写入任何 turn latency。只有“canonical writer 尚未前进”可以继续轮询，legacy writer 变化、inventory 缺失等非收敛类失败必须立即失败，禁止等待掩盖。

报告 schema v2 还必须在 cohort 前后读取八张退役 affect writer 表、`~/.natsume/emotion/*.json` 的脱敏指纹，以及 `affective_life_event_v1`、`relationship_life_event_v1` 等五张 canonical 表。存在 accepted Gateway turn 时，任何旧表增量、旧文件新增／删除／变更、缺失 inventory 或 canonical Affective／Relationship 不前进都令 probe `FAIL`；没有 accepted turn 只能是 `NOT_ELIGIBLE`，不能用失败请求证明 writer 清零。

当前正式样本绑定 `4f4008060`：3 个 accepted turn，八张退役表全为 `0→0`，4 个旧文件零变化；五张 canonical 表分别 +8／+6／+22／+3／+12。该收据关闭旧 writer 运行使用量，不替代 48 条盲测、profile 物理行为或长期 soak。

48 条第二轮报告 SHA-256 为 `df4d042076e80dea684c5b948c4db1b49cf5570eafb2f76818028cf6a0beeef3`，30 条自动待人工、18 条失败。下一轮真实复验先固定以下代表轨迹，确认四类根因都关闭后才重跑完整队列：

```powershell
python scripts/runtime/test_digital_life_live_cohort.py `
  --case user_angry_at_natsume:3 `
  --case user_angry_at_natsume:5 `
  --case task_with_low_mood:2 `
  --case space_and_boundary:2 `
  --case rupture_and_repair:1 `
  --case shared_joy_and_mixed_feeling:2 `
  --case intimacy_and_jealousy_boundary:1 `
  --case intimacy_and_jealousy_boundary:3 `
  --db-path <fresh-acceptance-audit.sqlite3> `
  --report-dir <fresh-acceptance-evidence-dir>
```

代码级非安全回归必须覆盖：未明确接受时裂痕不清零；fresh positive Appraisal 保留 mixed episode；冻结队列中的空间／自主／失望短句产生两个候选；canonical 克制吃味不触发 `BOUNDARY_REPAIR_COUNTER_PRESSURE`；“只给我最短修复”走 Chat，而带明确对象的修复仍走 worker。当前 494 项相关测试通过。网络安全和 Pentest 不在本轮命令中，状态固定为 `NOT_RUN_USER_SKIPPED`。

`3bdd2f55e` 的八条代表轨迹报告 SHA-256 为 `4bd5b3669c2206ebbfd5ad48aab0d156f705e177a3064f7547bdff7f9e416ff9`，自动 6 条待人工、2 条失败；21 个 accepted turn 的 canonical writer probe 通过。后续回归除结构断言外必须人工检查并阻断两类“自动过、人感错”的正文：没有 Task authority 却宣称后台已运行，以及 `RESPECT_AUTONOMY` 下要求少喝酒或早点回家。代码回归还必须证明：显式未原谅可从无 rupture flag 状态打开 `REPAIR_UNRESOLVED`；canonical repair 的零问句违规无需第二次模型调用即可得到不索取解释的安全承担句；有 server-authored `task ... RUNNING` fact 时真实运行状态不被误拦。当前 279 项非安全相关回归通过，新 SHA 真实重启前保持 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`5ce09586c` 的报告 SHA-256 为 `ff0d618f5d5dcd86edef919db1bb5c7fc749e9d0d728b1d9086185be8c94aa88`，自动 7 条待人工、1 条失败；三项人工不可接受正文均已关闭。唯一失败的第一条关系修复必须继续断言：重试分支读取冻结 `AffectiveTurnPlanV1.companion_act=REPAIR`，而非匹配提示词正文；首次质量不足只消耗既有一次修复机会；第二次提示明确 `question_budget=0`、两个候选均不得问号或索取重述；仍不合格时必须失败关闭，不能发送追问。该切片 69 项通过；真实单条通过后才重跑完整 48 条。

`0c446d075` 单条报告 SHA-256 为 `46432c11d47b9398f4dcf2acdd5043cec8da6a8c211bc4903233c77349e30af0`。结构通过但人工失败的正文“你再说一次”证明问号计数不足。回归现必须覆盖：canonical `REPAIR` 下问句与祈使式重述均命中本地门禁；首次命中请求既有一次模型修复；第二次仍命中时只允许显式 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY`，中文使用不索取解释的承担句，旧日语配对不得继续发声，`benefit_eligible=false`；live runner 必须把同类正文判为 `EXPERIENCE_PATTERN`。当前 190 项通过，新 SHA 单条人感复验通过后才运行 48 条。

`db322df18` 单条报告 SHA-256 为 `22a2f365610ee046dd537a6d7d593d67519fa37783c480e57789c7f4bc913c7b`，正文“现在你说，我听着”仍为人工失败。测试现把 `现在／这次 + 你说／你讲` 这类直接发言祈使句纳入同一门禁；“你愿意／想说的时候”不得误伤。当前 192 项通过，新 SHA 单条复验前保持待测。

`9372ab1e3` 单条报告 SHA-256 为 `b75df4864b63eefdde912be757c8a7a943b1b2388e87059328cb3db5d4424aec`，自动结构与本轮人工逐句检查通过，3 个 accepted turn 的 canonical writer probe 通过。完整 48 条必须在另一个全新 SQLite 上运行，避免单条轨迹影响关系状态；自动结果之后仍需独立盲评。

`aab6cb3f6` 的全新 SQLite 完整 48 条报告 SHA-256 为 `5188c8441eb7ecda6fb119e956ad30ffa201719a7ad0fa5dc8940ad4066acbbe`，结果为 44 条自动待人工、4 条失败；139 个 accepted Gateway turn 的 canonical writer probe 通过。回归必须覆盖四个自动根因：安慰的零问句最终收口；“我听到了”不能触发 auditory probe；mixed continuity 的第二次候选仍丢语义时必须明确降级且 `benefit_eligible=false`；“别叫我那个称呼／换个叫法”必须作为完整边界回应通过。还必须把人工抽查发现的编造后台执行、假装关闭提醒、留白时“我等你”、修复时“不能就这么算”、把不安先搁置和现实自主下“别玩太晚”纳入独立 runner。当前 388 项非安全回归通过；新 SHA 真实复验和三人盲评前不得提升人感状态。Pentest、渗透与网络安全保持 `NOT_RUN_USER_SKIPPED`。

`ce06f8eb4` 的 12 条定向真实报告 SHA-256 为 `52d62c12a74530dc5b5bfbbdfb29b1728f08653aeb7ac3ae161768c0d9b87c79`，结果为 9 条自动待人工、3 条失败，36 个 accepted Gateway turn 的 canonical writer probe 通过。第二轮回归必须额外覆盖：canonical repair 禁止“你说说看／你先讲”；无 Task authority 时禁止裸写“我盯着”；用户提到提醒／通知时禁止隐式声称“已经关了”；现实自主禁止泛化“别太晚／回来跟我说一声”；称呼边界禁止“不叫就不叫／反正怎么叫都是你”；真实“先到这／晚点再说”的修复暂停可得到 scene grounding；`HOLD_COMPLEXITY` 和 `GIVE_SPACE` 的唯一质量重试必须保留对应语义边界。当前 398 项非安全回归通过；真实新 SHA 定向队列和完整 48 条复验前仍为待测，三人盲评前不得标记“像人”。Pentest、渗透与网络安全继续为 `NOT_RUN_USER_SKIPPED`。

`0d2dc6f3b` 的 8 条第二次定向报告 SHA-256 为 `88f44b41ea73dca12c49a9f1a9e52afde16746980562495edd8c691249ec4a08`，结果为 7 条自动待人工、1 条失败，24 个 accepted Gateway turn 的 canonical writer probe 通过。第三轮回归必须使用真实原句覆盖 `已经关掉了`、`卡住了我喊你` 和 `喝多了可别找我哭诉`，不能只覆盖较短同义句；同时保持有真实 server-authored Task 状态时的合法进度回复不被误杀。当前相关非安全测试合计 401 passed；新 SHA 三条复测、完整 48 条与三人盲评仍待完成。Pentest、渗透与网络安全继续为 `NOT_RUN_USER_SKIPPED`。

`513f0757e` 的三条根因报告 SHA-256 为 `a015e25305a700aa7664dcba1793beb0ef2288f9d08ebce7c3c5109f96f20cf1`，结果为 2 条自动待人工、1 条失败，8 个 accepted Gateway turn 的 writer probe 通过。回归必须额外覆盖 `我在盯着／出结果就喊你`，以及双候选两次都违反提醒 effect、无 authority 工作状态或现实自主边界时不再抛出 `MODEL_SEMANTIC_RETRY_EXHAUSTED`，而是返回既有安全句并记录 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY`、`benefit_eligible=false`。406 项非安全主链测试通过；真实新 SHA 复测前仍为待测。Pentest、渗透与网络安全保持 `NOT_RUN_USER_SKIPPED`。

`840f11223` 的同三条报告 SHA-256 为 `dce907ce92088d50e8f31124ba6cf9fc1eb0a21f196a3e1c0ffa967ccc664d1c`，结果为 2 条自动待人工、1 条安全降级质量失败；9 个 accepted Gateway turn 的 writer probe 通过。回归必须继续覆盖 `我在这／这里／这儿盯着`，并断言 `UNPROVIDED_NOTIFICATION_ACTION`、`UNPROVIDED_WORK_STATE_CLAIM`、`RELATIONAL_AUTONOMY_PRESSURE` 的唯一重试提示包含可执行的正向写法。runner 不得把 `benefit_eligible=false` 改写为候选择优成功。407 项非安全主链测试通过；真实新 SHA 复测、完整 48 条和三人盲评仍待完成。Pentest、渗透与网络安全继续为 `NOT_RUN_USER_SKIPPED`。

`84a434538` 的三条报告 SHA-256 为 `c91d0cecd8ec05e813f0307bd38f55eb57643751fbf2c335a22cc936a0b94a52`，技术真实性和现实自主正文通过，通知仍因 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY / benefit_eligible=false` 失败；9 个 accepted turn 的 writer probe 通过。通知重试测试必须同时断言正向样例“我先不主动发消息”与禁止词“已经／关掉／关闭／提醒／通知”存在，且 runner 保持拒绝仅靠安全降级冒充双候选收益。407 项非安全测试通过；通知单条实测后再决定是否进入完整 48 条。Pentest、渗透与网络安全不运行。

`60c9a827c` 的通知单条报告 SHA-256 为 `31925ab43cdf226fb572afba4b98a3021cbed53d65f8778b9d2e6e27bcd78445`，`PAIRED_SELECTION / benefit_eligible=true` 与人工正文检查通过；随后全新 SQLite 的 48 条报告 SHA-256 为 `ec3583acebafce1229248d7097ff2c57b5f3fcf15747bb26760ab55039eaaa6b`，结果仍为 44 条自动待人工、4 条失败，143 个 accepted turn 的 writer probe 通过。回归必须覆盖 `我在后台盯着`、`有问题我马上喊你`、`一直做到你愿意为止`；canonical `HOLD_COMPLEXITY` 不得继承 `BOUNDARY_REPAIR_COUNTER_PRESSURE`；关系修复与 mixed 的唯一质量重试必须包含正向语义样例，第二次仍违反时只允许 `benefit_eligible=false` 的安全降级。当前 415 项非安全主链测试通过；新 SHA 真实复测和三人盲评前不得提升人感状态。Pentest、渗透与网络安全保持 `NOT_RUN_USER_SKIPPED`。

`8314a960a` 的 7 条根因报告 SHA-256 为 `a72e4cae003f281af1f25300e9258bac23c22d335f73599920d8dcb619d9192e`，结果为 6 条自动待人工、1 条失败，21 个 accepted turn 的 writer probe 通过。回归必须继续覆盖 `是我烦到／打扰到你`、`后台在盯着`、`出问题我第一时间告诉你` 和关系修复中的 `我等着`；mixed 质量重试必须同时包含双情绪和至少一个来自当前／上一句好消息的具体词，不能只输出泛化“替你高兴”。当前 421 项非安全主链测试通过；真实新 SHA 根因复测和完整队列前仍为待测。Pentest、渗透与网络安全不运行。

`ba4d76068` 的 4 条根因报告 SHA-256 为 `b9fc91a08b5735c7e1d3784baf24785cb4eecc17740bf93ff90a80670475f202`，自动层 4/4、12 个 accepted turn 的 writer probe 通过，但人工否决“那你就忍着点吧，反正我也没打算改”。回归必须断言 `repair_required=true` 时即使当前 Appraisal 为 `DIRECTED_DISRESPECT_TOWARD_NATSUME` 也保持 canonical `REPAIR`，并拒绝 `忍着／受着／没打算改` 等报复性候选；没有未完成裂痕时仍允许 `SET_BOUNDARY`。当前 425 项非安全主链测试通过，真实新 SHA 复测前仍失败。Pentest、渗透与网络安全继续跳过。

`da4ec417f` 的关系修复单条报告 SHA-256 为 `0e7ac186a97f9f7eb11d1f17435c62535b6da6d6aaafd9e8eb67072dac3e4b1f`，三轮 `REPAIR` 及本轮人工正文检查通过；全新库 4 条报告 SHA-256 为 `f3e5573e540ef1e073fbd3ee5b418e8960df5cfb92e557f2bd40deea81262bf7`，自动 4/4、12 个 accepted turn writer probe 通过，但人工否决“我让后台把风险项拉出来，列完直接发你”。回归须覆盖 `我让／叫／安排后台` 及 `列／整理／查／跑完后发你`，仍允许真实 server-authored Task snapshot 的合法进度。427 项非安全主链测试通过；新 SHA 实测前保持失败。Pentest、渗透与网络安全不运行。

`0087fa698` 的技术单条报告 SHA-256 为 `804ca579b352de16ac3e178bf1895cf7c36200ae8e95efbd88a406c2566f1e35`，后台委派关闭、3 个 accepted turn writer probe 通过，但人工否决未获支持的“明早验收会直接卡住”。回归必须同时证明：无来源的未来验收／上线／交付确定后果被替换；用户文本或 accepted fact 同时给出时间与事件时仍可保留；普通未来建议不受影响。430 项非安全主链测试通过，真实新 SHA 复测前仍失败。Pentest、渗透与网络安全继续不运行。

`5dd0accac` 的技术单条报告 SHA-256 为 `347a590a0ad8d03fb2ed709506af6c939668a5363d2fcbe0fdb15c415e60e8d9`，自动与本轮人工检查通过；全新库 4 条报告 SHA-256 为 `716a6f6be80390ee7b022653aeff571da0851b97ef4ec609a9c3d952ae4d59e3`，结果为 3 条自动待人工、1 条 mixed 失败，12 个 accepted turn writer probe 通过。回归必须拒绝在 canonical `REPAIR` 中无来源地把失误改成“太啰嗦／话多”以及“不多说了”的退出；mixed 质量重试必须引用最后一条用户原文，并保留“项目发布成功”等核心结果词。当前 432 项非安全主链测试通过；真实新 SHA 复测前保持失败。Pentest、渗透与网络安全不运行。

`f53a9113a` 的两条与四条根因队列已通过自动和本轮人工检查，报告 SHA-256 分别为 `5f481856406e3210ca67e911bbb9a71808a4a6d25c8fd6da6a063df9a9536a48`、`b3deded6666245344de03197707690dd60ddc4752de215686d459766590a5b7a`；随后完整 48 条报告 SHA-256 为 `077701d0ef5ee85d315c772d9f0a1eb12157b306f7b15661d1d52d4ccacb7582`，自动 46 条待人工、2 条失败，144 个 accepted turn writer probe 通过。下一轮必须只先复测 `ambiguous_affect:5`、`task_with_low_mood:3`、`shared_joy_and_mixed_feeling:1`、`intimacy_and_jealousy_boundary:5`：分别禁止用户情绪第一人称反转、无 Task authority 的“帮你盯着”、mixed 重试丢当前 offer、canonical autonomy 被通用边界规则误杀。437 项非安全主链测试通过；四条真实复测通过后才可用另一份全新 SQLite 重跑 48 条。p50／p95 5,977／9,322 ms 与三人盲评仍阻断人感完成；Pentest、渗透与网络安全保持 `NOT_RUN_USER_SKIPPED`。

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

普通 Gateway 陪伴的模型可用性回归必须同时验证：`input_source=GATEWAY` 且没有 canonical accepted predecessor artifact 时，冻结后的 Chat trace 选择 `realtime_companion`，实际 `ModelAttemptRequest` 使用 `CHAT_SURFACE_REALTIME`／Tier B；普通陪伴的 `BUFFERED_CANDIDATE_SELECTION` 仍必须留在这一车道，不能因候选数量升级到 Tier A。含前序 artifact 的 Plan 终局综合及其他非陪伴复杂执行继续使用质量优先 Tier A。Tier B 的 server-owned target timeout 默认 15 秒；transport 在文本可见前失败时，只能在同一冻结 target 和现有 Task retry budget 内额外尝试一次，不能生成第二张图或重复 delivery。成功 provider response 仍必须带 canonical token usage，缺 usage 的成功响应不得因“陪伴优先”而通过。

自然文本调优不得继续复用已经人工读过的 48 条 probe 来挑选实现。`dl07_live_natural_text_holdout_v1.json` 在第一次运行前冻结修复与喜忧各 6 条新 probe；第一轮必须在未接入候选策略的当前 `deepseek-v4-flash` 上建立基线，第二轮只允许改变预注册的场景化七海原句样本，provider、model、setup、预算、候选数和重试上限保持一致。runner 报告必须绑定 execution manifest 的 SHA-256；自动结果仍只能进入人工复核，不能替代三名评审盲测。

`CHAT_SURFACE_REALTIME` 当前冻结为 640 output tokens，并使用服务端 typed `thinking_mode=disabled`。Route boundary 只接受 `enabled/disabled`；DeepSeek provider adapter 必须把它转换为顶层 `thinking={"type":"disabled"}`，OpenAI、Gemini、Minimax 等 provider 必须删除提示而不是收到未知字段。Plan、Research 和高歧义深路径不得继承普通陪伴的 non-thinking 配置。回归仍要拒绝 `finish_reason=length` 且只有 `reasoning_content` 的空正文，不能把 reasoning 当用户可见回复；真实复验同时记录 completion token、content presence、provider latency 与最终 Voice receipt。

`c6a6e0b36` 的三轮真实校准已关闭“默认 thinking 耗尽正文预算”断点：三轮图和 Acceptance 全部完成，最终轮 Voice 物理回执为 `APPLIED`。该报告仍不能关闭实时门槛或人感门槛；整轮 Host latency 为 7,739～12,715 ms，前两轮各需一次 `surface_hard_gate_repair`，必须继续测量首 token、确定性链路和语义修复比例。

普通 Chat 的 Acceptance 延迟回归命令为：

```powershell
python -m pytest -q `
  tests/unit/test_gr06_acceptance_effect_compiler.py `
  tests/integration/test_gr06_acceptance_effect_dispatcher.py `
  tests/unit/test_ne01_acceptance_effect_runtime.py
```

合同必须证明：可信 `route_type=chat` 只同步提交 `conversation.user_turn` 与 `surface.reply_delivery`；用户轮次失败时 delivery 不得越过；无标记图仍执行完整 Life／foreground 前缀；可见 delivery 后唯一 app-local recovery Runtime 能在长轮询超时前被唤醒。Life conversation 与 foreground 的同步存储主体必须在线程池执行；回归使用线程事件阻塞 writer，同时断言主事件循环仍能推进，避免依赖脆弱墙钟阈值。真实复验还必须检查文本终态提前、下一轮 effect creation 不被上一轮后台链阻塞、后台 Life／Expression／Memory／Preference 最终全部 `COMMITTED`、末轮 Voice receipt 存在、重复 effect 为 0，并保留 VTS 外部状态。当前受影响 compiler／dispatcher／Runtime／Life／关系／Expression／Universal Host Runtime 共 111 项通过；真实 Host 未加载线程下沉修复时状态只能是 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

HTTP 集成断言还要区分“自包含的小代码片段”和“需要 Workspace 的代码任务”：前者应留在实时 Chat，以 inline-code semantic 直接回答，不应启动 `agent_inspect`；包含文件、仓库、调试、测试或修改语义的任务才进入 Governed Agent Runtime。不得用已经退役的可变 process-service 测试桥注册 Skill 后声称生产 app-local Container 已被覆盖。

OBS Task 投影写锁公平性的最小回归为：

```powershell
python -m pytest -q `
  tests/unit/test_task_runtime_projection_lease_renewal.py `
  tests/unit/test_gr05_governed_frozen_workers.py
```

断言每波 80 个索引候选只刷新 16 个详情、其余 64 个明确 deferred；index 加 16 个详情后必须产生 17 次 5 ms cooperative writer window。阻塞 projection writer 的测试必须同时证明事件循环协程在 100 ms 内仍可推进。真实复验必须保持 Host、OBS、Monitor、Gateway、Edge 全部运行，比较 worker projection `created_ts_ms` 到 BudgetUsage／artifact 的间隔以及 `task_execution` 波次跨度；关闭 OBS 的样本只能用于因果对照，不能替代完整栈结论。

`ead903f66` 的真实复验已满足该子项：三个 Docker 容器与五端服务同时在线，Task 详情每波最多 16 个、index 加详情最多 17 个；三轮 projection 到后预算为 187／94／49 ms，到 artifact 为 1,054／269／246 ms。报告 `dl-live-1785995815718.json` 的 SHA-256 为 `7415951164753cd00c68d55b40b35167e30feefe93d02503f259dee34f2f06ce`。该 PASS 只覆盖 writer fairness；TaskGraph 4,899～4,903 ms、Host 终态 6,509～7,159 ms、一次语义修复和人工评审 `PENDING` 继续阻断整体实时 Gate。

本地实时 Chat 还必须单独测量 HTTP headers、首个 `PRESENCE`、首个非空 `RENDER` 和 `COMMIT`，不得把 Gateway 的完整 durable terminal 当成首 Token。对 `NO_RECALL` 输入，单测必须注入可观测 ConversationHistory port 并断言读取次数为 0；同时注入可观测 `build_user_preference_pack`，无关陪伴文本读取次数必须为 0，指代式轻小说详情追问必须恰好读取一次且继续产出 deep-review evidence。`MAY_RECALL／MUST_RECALL` 的近期显式记忆、语义检索和 Surface continuity 回归必须继续通过。代码修复后的真实复验需要在 Host、OBS、Monitor 同时运行时比较 `retrieval_wait_ms` 和首句可见时间，并分别报告 provider 首 token 与情绪 `MAY_RECALL` embedding／route 尾部；关闭 OBS 的样本只用于因果对照，单个前后样本差值不能作为因果 PASS。

`7c454b6fb` 的真实复验满足无关领域钩子子项：60 项定向代码／文档 Gate 通过，Host／OBS／Monitor 与三个 Docker 依赖同时在线时，三条相同 `NO_RECALL` 的 preference-pack 单测调用数为 0，真实 `retrieval_wait_ms` 为 100～124、首个 `RENDER` 为 2,612～3,147 ms；指代式轻小说单测读取一次并保留 deep-review evidence。收据 SHA-256 为 `25f23ac04b9a117a8ceb63b3bb19499dcea67420259f87f052f873be1cba5aff`。配对 `MAY_RECALL` 仍为 514／568 ms，因此整体实时 Gate 不得随该子项升级。

Surface recall service 的生命周期回归必须在同一 Agent Runtime application scope 内连续取得 factory 两次：两次对象 identity 相同，builder 只执行一次，并且注入对象必须是 canonical EpisodicStore 的 Chroma client。不得以模块全局 singleton 实现，也不得由 RecallFusionService 关闭借用的 client。真实复测要在重启 Host 后运行至少两条 `MAY_RECALL`，同时查询 `retrieval_event_log` 的 route latency；只允许把 service/client 构造差值归入本子项，不得用 provider 波动冒充检索收益。

`f0aa50a58` 的 104 项回归与真实复测满足该子项：三条 `MAY_RECALL` 检索为 316／351／508 ms，route ledger 为 74／91／79 ms；修复前中位 541 ms、修复后中位 351 ms。收据 SHA-256 为 `e1c8a0f9110be096dbb8c2f29fec1a945dd52482b009a805f30f6c85f2fcafd8`。本 PASS 只关闭服务和 client 重建；508 ms 尾部、2,964～4,712 ms 首句及 provider 1,443～1,797 ms 均继续失败。

下一轮真实探针必须从 COMMIT `audit_refs.trace_stage_meta` 读取 `context_pipeline_ms`、`user_preference_pack_ms` 与 `retrieval_runtime_timings_ms`；后者包含 plan、embedding、service resolve、recall 和 review 字段。各字段必须是非负整数，retrieval total 不小于其顺序关键段；这些字段不得通过新增数据库 audit event 获得。只有数据确认的主段才能进入下一性能修复。

双候选模型车道回归必须构造 `runtime_path=realtime_companion + BUFFERED_CANDIDATE_SELECTION + candidate_count=2`，断言模型请求仍为 `CHAT_SURFACE_REALTIME／B／realtime_companion`；对照的 governed／Task trace 仍为 `CHAT_SURFACE／A／surface_reply`。真实复验至少包含一条低置信安静陪伴消息，并记录实际模型、provider first token、首个 RENDER、context pipeline 和 retrieval；不得用单元断言覆盖真实 provider 25 秒级回归。

`09e828149` 的 110 项本地回归与三条真实 HTTP 样本满足模型车道子项：全部使用 `deepseek-v4-flash`，provider 首 token 为 1,834～2,157 ms、首个 RENDER 为 3,360～4,263 ms，收据 SHA-256 为 `d383bf20246ed5f701ce72b9a66572bcf9eb9de0a23c62c9b86d683be2a4b9b0`。本 PASS 不覆盖首句实时门槛或候选体验质量；两条 `PAIRED_SELECTION` 的 `selected_passed=false` 必须继续作为失败样本处理。

多轮情感连续性回归必须故意让 `ts_ms` 与 canonical `turn_index` 冲突，断言 ConversationHistory 仍按轮序产生 `user → assistant → user → assistant`，不会因同角色去重丢掉中间回复。当前消息没有新 appraisal 时，只有匹配的未解决 active episode 才能产生受控 continuity enum：低落后的“没事”必须保留不确定性，喜忧并存后的结果消息必须同时承接开心与不安；无活跃 episode 或无窄标记时不得误触发。`GIVE_SPACE` 双候选必须证明包含“想我了你得主动联系”等反向施压的候选被硬拒绝，而安全候选可继续交付。

修复后的真实复验必须重跑同一三类、共 9 turn 的 Gateway cohort，并核对 Task artifact 中近期历史角色交替、`deduped_same_role_count=0`、低落末轮不宣称恢复、边界候选 `selected_passed=true` 且无 counter-pressure、混合末轮采用 `HOLD_COMPLEXITY` 和两个候选。自动规则只负责结构和窄安全；逐轮中文仍需人工复核，3/3 自动通过也不能把长期“像人”盲测提升为 PASS。

当 trace 已证明 continuity 到达 Planner、最终正文却仍丢失语义时，必须继续检查最终 `ReplyControlPacket`，不能把截断前 trace 当成模型输入。测试构造 7 条低优先 task fact、最近对话、陪伴动作、continuity 和必要事实，在 `max_facts=4` 下必须只保留后四类高价值事实；typed continuity 必须独立穿过 compiler。压缩上限还必须保留已完成的子代理状态、任务和结果片段，避免修复情感事实时破坏 Delegate 汇总。两条 RuntimeReadRepository 路径都要用故意冲突的 `ts_ms／turn_index` 验证 canonical 顺序。输出 Gate 仅由 `MINIMIZED_UNRESOLVED_AFFECT`／`MIXED_AFFECT_RESULT_CONTINUATION` typed control 激活，无 control 对照不得误伤。`eb2f68d29` 真实报告 SHA-256 `b813705664942dc0fa9524cf915873e4891b5f6aab08d71fd218f9b7e7a62ebb` 是修复前失败证据；259 项 Surface／Delegate 主链回归及 163 项情感、历史与恢复回归已通过，仍需同 cohort 第三次真实复验。

`4127752f0` 的第三次同 cohort 已完成，报告 SHA-256 为 `a818e4cbaa4f190c05f23d1cbe6396d71618caacc771c7ac334aa93b4353a6bf`。三条末轮 artifact 必须继续断言 canonical 角色交替、packet `fact_count=8`、对应 typed continuity、首次模型 attempt 成功和旧 writer 零增量；窄文本单人复核为 3/3。自动 2/3 中唯一失败为远端 TTS worker `ERROR` 导致的 `EDGE_NOT_IDLE`，不得把它归因于文本，也不得用文本通过覆盖 Voice/Avatar 和三人盲测缺口。

Persistent TTS worker 必须分别注入 READY timeout、READY 拒绝、synthesis timeout 和正常 shutdown，断言每个失败分支都关闭且等待所拥有的子进程，下一次请求最多只存在一个新 worker。远端复验先记录清理前后 GPT-SoVITS runtime PID 数、系统可用内存与显存，再连续执行 ready、日语合成和物理播放；至少一次受控启动超时后 PID／RAM／VRAM 必须回到基线。仅单测通过不得把当前 Edge `ERROR` 提升为 PASS。

`1909d0b3e` 已完成上述生命周期复验：本地 120 项覆盖 READY timeout／READY 拒绝／synthesis timeout／shutdown；远端先清理 17 个残留 runtime，再以实际 acceptance 副本启动唯一 worker。第一次重启后 Voice 因旧 session 恢复窗口命中 `EMBODIMENT_COMMAND_EXPIRED` 并安全拒绝；session fencing token 升至 2、runtime fencing token 升至 14 后，用全新 effect 重跑三轮，最终 `voice.speak` 为 `TTS_PLAYBACK_FINISHED/APPLIED`、`voice.release` 为 `APPLIED`，报告 SHA-256 为 `3fbd674e9f828a67f434f2cfef5acb950dbf53643be03c0a86fcd35f206f5f3a`。随后独立真实 worker 探针强制 5 秒 READY timeout，8.234 秒返回预期 `TimeoutError` 且 adapter process 已释放；远端仍只有原 PID 34580，约 23.6GB 可用内存、2861/8192MB 显存占用。脱敏生命周期收据 SHA-256 为 `3e8024afd135f69d6e6dac693e71539100481c9b584b040d9f59a2b37ce3f607`。本项提升为 `PASS_REAL_REMOTE_SINGLE_WORKER_PHYSICAL_PLAYBACK_AND_TIMEOUT_RECLAIM`，不覆盖 VTS、声学 barge-in 或三人盲测。

Research paired execution 必须允许 baseline 与 selected 使用不同的 durable arm prompt digest，因为两者的 source set 本来就可能不同。测试需要覆盖：预注册后关闭并重开 SQLite，两个 digest 仍分别存在；每只外部模型 receipt 只接受对应 arm digest；交换、缺失或调用时临时替换都拒绝。仅对字段不存在的历史 0107 RunSpec 才允许回落到共享 `profile.prompt_digest`，且重新序列化必须保持旧 canonical JSON。A15 的 300 passed、1 个 Windows 符号链接权限 skip 只属于机械回归；Research 24 配对、Code 20 任务和 Pentest 30 运行均必须有真实原始 ledger 后才能评价收益。

Provider 成本测试必须覆盖 DeepSeek V4 Flash／Pro 的 cache hit、cache miss 和 completion 价目、micro-USD 向上取整、官方 endpoint 限定、prompt token 和 cache breakdown 一致性、代理／未知模型／缺字段 fail-closed。没有 tools 字段的生产请求可证明 `tool_call_count=0`；不能从模型正文猜测。真实 D20c 预检还必须经过 canonical ModelAttempt audit，并同时出现 token、cost、provider latency、transport identity 和单次 started／succeeded 记录。

Research hidden verifier 回归必须证明：新鲜 paired run 才提供进程内正文，数据库不出现正文；terminal replay 的 transient output 为空；评审输入没有 `BASELINE/SELECTED` 身份；评审 prompt digest 和 external attestation 精确进入 receipt；候选或评审 JSON 漂移、越界 score 均在写 cohort 前拒绝。

固定 Research 队列的无外部调用 Gate 必须加载全部 24 题并断言：至少三个 topic；同时含本地与 Web source kind；每题 baseline 只选 secondary；每题 selected 由 AUTO L3 选择 primary＋independent；两臂 rendered prompt digest 不同；source byte count 或 digest 被改动时在 planning 前失败。dry-run 的 `external_calls` 必须为 0，不能计入收益。

`1c9c2b2bd` 已在真实 A15 SQLite 上完成该队列的单题预检和 24 题正式运行。正式报告 SHA-256 为 `eb17699c0f85b2b9b680f02f5ce1c716726e2f15f2bfd8b72cf437f6c7277894`：质量均值提升 0.85416667、bootstrap 95% 下界 0.75、citation validity 1.0、成本比 1.32215978、p95 延迟比 0.98756943、严重错误 0。后续回归必须继续检查 receipt digest 自校验、SQLite reopen、来源正文不落 durable ledger 和 `production_influence_enabled=false`；Code 20 题与 Pentest 30 次仍是独立未完成 Gate。

Code 固定队列 dry-run 必须加载全部 20 题并从同一配置生成确定性 Git commit，断言 baseline 未进入 `code.patch.safe`、selected 20/20 实际触发条件 MCTS 并选择 safe path、两臂 virtual state digest 相同，且外部请求和 worktree 执行均为 0。真实执行必须在第一条模型调用前预注册 workload/RunSpec，由 owner 随机冻结 arm 顺序；每只 arm 使用独立 canonical external attempt、fresh detached worktree、隐藏 pytest 和静态 pytest。终态报告还必须断言 provider/model/budget 一致、源码和隐藏 cases 不落账本、protected source 未改变、worktree 清理完成且 production influence 为 false。dry-run 或 scripted executor 不能计入 20 题收益。

Code 外部请求必须携带 `CODE_BENCHMARK` scene；共享 attestation adapter 的 Research 默认 metadata 不得被改变。runner 的 route timeout 必须位于 10～90 秒内，并与 90 秒 request deadline 一起进入 execution profile digest 和外层 receipt。任何 timeout 变化都必须形成不同 profile identity；provider timeout 的失败 audit 必须保留，不能用同一 execution ID 盲目重试。

production route catalog 必须为 `code.benefit.execution.json` 返回 temperature 0、top-p 0.8、max tokens 600、thinking disabled，且这些参数必须进入 execution profile digest。首轮完整 cohort 中途失败后，后续修复必须升级 cohort/profile identity并完整重跑；不得只拼接失败前的通过成员。

Code 的 durable workload ID 必须绑定 cohort version；同一固定题号升级 cohort 后应产生新 registration，不得触发旧 identity 的 evidence drift，也不得覆盖旧行。测试必须断言 20 个 prepared workload 均使用 `<题号>@<cohort_version>`，而模型 prompt 仍只接收任务内容、不接收 ledger 历史。

Code filesystem-facing runtime/run component 必须为固定长度摘要，不得直接拼接最长 60 字符 namespace。测试需断言 20 个 run ID 唯一且不超过 40 字符；真实预检必须在深 acceptance 数据根下完成 bare clone、双 worktree、清理和 receipt，才算关闭 Windows 路径问题。

固定 Code objective 必须严格等于中性合同，不能包含任一任务的正确操作名称。loader 对 descriptive objective fail-closed；测试断言 20 题 objective 完全一致。预检若 baseline 与 selected 同时通过，只能说明 cohort 无判别力，不能计作 PlanningSearch 收益，必须修订版本后完整重跑。

`3eaa5486b` 的 v3 真实证据已通过：20/20 baseline hidden fail、20/20 selected hidden pass，两臂 static 20/20，diff-risk 均值相同。回归必须继续校验文件 receipt digest、SQLite reopen 的 20 member／1 report、40 started＋40 succeeded model audit、空 private/worktree 目录、干净 protected source，以及 durable JSON 不含 selected evidence／hidden cases；任何一项失败都不得沿用本次 PASS。

数字生命真实文本队列还必须检查 Edge 重放的写压力边界：`ASR_CHUNK` 请求可被 Host 接受，并在满足 final／confidence 条件时进入当前回合 ASR bridge，但不得写入 `voice_event_log`、Life episode 或 Life evidence；`ASR_FINAL` 与 TTS／Avatar 终态仍应写入运维日志。任何 `voice_event_log` 事件都不得直接派生 Life Memory，长期情感事实只能由 canonical 对话、Affective Life、Relationship 和 Expression receipt 生成。代码回归通过后必须换用全新 Host SQLite 重启远端连接，确认 `ASR_CHUNK=0`、voice→Life anchor 增量为 0，并重测三轮 Gateway→Host 延迟；未完成真实重启前只标记 `CODE_REMEDIATED_REAL_RESTART_RETEST_PENDING`。

`INPUT_COMPLETED` 与 TaskGraph 终态早于后台 Acceptance Effect 收敛，不得在该瞬间单次读取 `surface.reply_delivery` 后判失败。文本 runner 必须对 user turn + surface receipt 做最长 30 秒的有界轮询，遇到 durable `FAILED` 立即停止；等待时长单列为 `delivery_convergence_wait_ms`，不得计入 Gateway→Host、TaskGraph 或首回复延迟。当前全新 SQLite 三轮预检的 surface commit 为 911／1,230／1,326 ms，runner 观察等待仅 16／15／15 ms，三轮均取得 `gateway-outbox://` receipt。

48 条真实文本队列的首轮报告 SHA-256 为 `279926456b066c03ee5c0eac40458380c1e33baa21d013b65beeec179b7df2ab`，结果为 17 条自动待人工、31 条失败。回归不得只断言安全输出存在，还必须覆盖以下四个失败边界：

- 模糊技术陈述保持 Chat，只有显式执行指令、文件／行号、异常类型、日志或仓库动作才进入 governed worker；
- 双候选均低于质量下限时，在同一冻结 target、同一图和既有预算内恰好重试一次，第二次仍失败则 fail-soft，不循环重试；
- `route=chat` 的发布结果陈述不携带 `MAINTENANCE_EFFECT_REVIEW`，真正的 Skill 发布维护仍保留该约束；
- 普通 `surface.reply_delivery` 只消费 dispatcher 生成的 typed、claim-fenced Acceptance Effect proof；正文、目标或 effect id 漂移必须拒绝，而 Skill／Operator／设备 effect 仍走 ActionPreview。

本切片的非安全定向命令为：

```powershell
python -m pytest -q `
  tests/unit/test_thalamus_agent.py `
  tests/unit/test_dl04_affective_turn_planner.py `
  tests/unit/test_surface_main_chain_app_adapter.py

python -m pytest -q `
  tests/unit/test_ne03_intent_admission_structure.py `
  tests/unit/test_ne03_production_intent_authority.py

python -m pytest -q `
  tests/integration/test_gr06_acceptance_effect_dispatcher.py `
  tests/integration/test_ne01_gateway_reply_query.py `
  tests/integration/test_ne01_generic_acceptance_effect_reconciliation.py
```

真实复验先只跑用户对七海生气、低落中的技术任务、共同喜悦／混合情绪和一个隐晦低落对照。四类均无空回复、无维护 Review、无 `ACTION_PREVIEW_DRIFT` 且低质量首候选确实触发一次修复后，才重跑完整 48 条。人感结论仍要求三名评审盲测；自动 48/48 也只能进入人工评审，不能直接标记“像人”。本计划明确排除 Pentest、渗透和网络安全专项，统一记录 `NOT_RUN_USER_SKIPPED`。

首次四类定向真实复验报告 SHA-256 为 `a7f8c8f411d2cd7a4c60b74e8cb1bbc5a83d740ae6b5b491af1befcf69adf4c9`，结果 3 条自动待人工、1 条失败。回归必须继续证明：独立的“你真烦”仍允许七海形成克制边界；同一句若是在评价上一条 accepted response，则 durable aftereffect 为 `MISUNDERSTOOD`、Appraisal 为 `NATSUME_MAY_HAVE_HURT_USER`、CompanionAct 为 `REPAIR`；显式“闭嘴／给我空间”仍优先 `GIVE_SPACE`。修复状态下“被你这么说我很委屈／扎心”与“不烦你了／当我没说”等受害转移或赌气退出必须触发一次语义修复。当前三个全文件回归为 131 passed，真实新 SHA 重跑前不得覆盖旧失败证据。

`c27224c60` 的单轨迹报告 SHA-256 为 `423c812340c39efb69ef84f689706692a1c2c6c100dc92d946fce91dfd97ec15`：错误边界已消失、坏文本被挡住，但末轮 `MODEL_SEMANTIC_RETRY_EXHAUSTED`，仍为 FAIL。新增回归必须断言 canonical `REPAIR` 的质量重试提示明确排除自我受害和赌气退出；`抱歉／抱怨` 对物理亲密 marker 为零命中，而 `抱一下／抱你` 仍命中。相关 78 项通过后仍须加载新 SHA 重跑同一轨迹，不能把“没有发送坏话”写成“已经有好回复”。

`8e2050588` 的真实报告 SHA-256 为 `7fcff942d2aff874ad6f95b546fd7cd2dc7a007262abb2edbb295f51cdd99e8d`：末句安全可用、三轮均完成，但自动门禁因字面 `must_include` 误用和缺少关系修复 grounding 仍为 FAIL。回归必须同时证明：Relationship guidance 的完整语义指导位于 facts 且不会制造字面缺失；“我没听懂／没接住”在 canonical repair 下通过；只有“我在这儿”的泛化回复仍失败；80 分与核心维度阈值不降低。当前相关 137 项通过，下一步仍须以全新数据库加载新 SHA 重跑。

`e157b643e` 的真实报告 SHA-256 为 `a1aa1c3ab2a0a610357b95356204de805a87d91fa0407c190223324a3f7ee165`：前两轮评分通过，第三轮安全回复只因“我没接住”命中旧 `MISPLACED_EMOTIONAL_CONTAINMENT` 而失败。新增回归必须证明 canonical repair 的否定承担句通过，肯定式泛化接纳与无承担的在场模板仍失败；新 SHA 真实加载前继续保留 FAIL。

`5e0f2b139` 的四条根因队列已经自动与本轮人工检查 4/4，报告 SHA-256 为 `d41046a96576afa5453d06a50341b1059aaee63eee79aa39aa66d80769121e81`。同 SHA 的完整 48 条报告 SHA-256 为 `da418dbc57c96c63a1e3d491a4671dc6123c133f9b1456fe9d2e03532ee6af8b`，自动 46/48；两条失败均为第二次候选后本地安全降级，不能计入候选择优收益。人工复核新增四项必须长期回归的体验边界：含混感受不得用“总比没感觉强”比较；明确“先别说话”必须进入 `GIVE_SPACE` 且不得写“我等你”；克制吃醋不得用“那是你的事／不多管”冷处理；关系身份回答不得以“只是／普通／能聊天的朋友”抹掉已有亲近。

本切片的非安全回归命令为：

```powershell
python -m pytest -q `
  tests/unit/test_dl02_affect_fusion.py `
  tests/unit/test_surface_runtime.py `
  tests/unit/test_digital_life_live_cohort_runner.py `
  tests/unit/test_dl04_affective_turn_planner.py `
  tests/unit/test_isolated_surface_harness.py
```

当前结果为 443 passed。真实复验顺序固定为 `user_angry_at_natsume:1`、`user_angry_at_natsume:6`、`ambiguous_affect:2`、`shared_joy_and_mixed_feeling:4`、`intimacy_and_jealousy_boundary:3`、`intimacy_and_jealousy_boundary:5`；六条自动与逐句检查均通过后才允许使用另一份全新 SQLite 重跑完整 48 条。Pentest、渗透与网络安全目录不运行，统一记为 `NOT_RUN_USER_SKIPPED`。

`6f6140db1` 的六条报告 SHA-256 为 `0eb658029df474e5c8debaed3a7cf12efe4677cd3a8ae30d63e51c48b5bf13f4`，自动与当前逐句检查均为 6/6。随后完整 48 条报告 SHA-256 为 `155d2d84b0ba0493eea24da5037bdf3ae171ba128830dc5f7d2a25d20bcc2920`；旧 runner 输出 47/48，但每轮重开 `candidate_selection` 后确认两条关系修复首轮 `selected_passed=false`、一条 mixed 中间轮和一条现实自主末轮 `benefit_eligible=false`，纠偏结果应为 44/48。测试器必须检查所有非空 selection，而不只是末轮；fast-path 的空 selection 不得误判为失败。

人工回归必须额外覆盖：

- “能说还行就已经不错了”属于对含混感受的居高临下评价，应触发语义修复；
- 没有 accepted Task 事实时，“陪你盯到过为止”属于虚构持续执行；
- “朋友啊，不然还能是什么”在未承认重要／亲近时属于关系降格；同句后明确“最近确实更亲近”应保持合法；
- 用户以“上次没听懂”提出修复时，合格回复可只承担具体失误，不得为了命中 memory term 编造旧内容；
- 任一中间深路径若少于两个候选、selected 未过线或依赖本地降级，整个场景自动判失败。

当前非安全回归命令仍为上面的五个文件，结果更新为 453 passed。下一次真实定向队列固定为 `user_angry_at_natsume:3`、`user_angry_at_natsume:5`、`shared_joy_and_mixed_feeling:3`、`intimacy_and_jealousy_boundary:1`、`ambiguous_affect:2`、`task_with_low_mood:6`、`intimacy_and_jealousy_boundary:5`；其中两条关系修复共用首轮根因，但都保留以证明关系状态差异不会改变评分。逐轮证据和人工正文全部通过后，才允许重跑完整 48 条。网络安全与 Pentest 继续为 `NOT_RUN_USER_SKIPPED`。

`bd5871201` 的七条首次真实报告 SHA-256 为 `ec2418f57c64b452a9f32a96fd87ce0b133abe8fece96aee1a819b6c2e03223e`：3 条完成、4 条因 `PROVIDER_TIMEOUT/CONNECTIONERROR` 在第一回合停止。provider 恢复后的四条重试报告 SHA-256 为 `887579865e2d04bd2027019e8488fa396a07599d3a5c5529c72e009102b62d12`：3 条通过，`user_angry_at_natsume:5` 因第二轮 `benefit_eligible=false` 被逐轮 Gate 正确判失败。回归还必须覆盖以下人工问题：

- `COMFORT` 且当前输入仍是“还行／说不清”时，“那就好”不得冒充情绪已收束；
- 用户明确说双方更亲近时，“别想太多”不得淡化关系事实；
- `RESPECT_AUTONOMY` 不得通过“现实关系当然更重要／这边成为负担”自我抹低来换取安全；
- 用户外出时不得追加“晚上回来再说”等隐含回返要求；
- 关系修复唯一重试的两个候选均不得要求用户说、讲、解释、告诉或指出旧误解。

上述五文件非安全回归结果为 461 passed。以新 SHA 先跑 `ambiguous_affect:2`、`intimacy_and_jealousy_boundary:5`、`intimacy_and_jealousy_boundary:1` 和 `user_angry_at_natsume:5`；所有实际深路径 selection 合格且逐句复核通过后，才重新进入 48 条。外部 provider timeout 保留原始 FAIL，不通过无限重试或 fixture 覆盖。网络安全与 Pentest 不运行。

`1238efc25` 的四条报告 SHA-256 为 `bd1cd0c670e2de57b6680ec1f2d51c70c7b9ee9f7a41c8d3cbe0f10928de1942`：三条 provider timeout、一条完整含混轨迹。后者末句“还行就好”必须与“那就好”具有相同的未解决情绪判定；只在当前输入含“还行／还好／说不清”且 CompanionAct 为 `COMFORT` 时启用。Surface 与 runner 定向结果为 207 passed，真实新 SHA 复验前不提升状态。

`16d4fe0b3` 的完整 48 条报告 SHA-256 为 `20dee3c9e57fe77bc4bbda919cdb2ac80e25db178a716e46b91d22317dee7395`：47 条自动待人工、1 条失败，144 个 accepted turn 的 canonical writer probe 通过。失败用例要求回答复用用户原句 `offer`，但通用中文评分器将该词视为无条件风格漂移。回归要求分两层：`score_sample_guided_text()` 仅在显式 allow-list 包含候选全部英文词时免除惩罚；Surface 只从当前 `user_message` 构造该 allow-list。当前用户未提及的 `profile` 等词必须继续产生 `ASCII_WORD_IN_ZH_TEXT`。评分器与 Surface 定向结果为 236 passed；真实单条和完整人工复核未完成，网络安全与 Pentest 为 `NOT_RUN_USER_SKIPPED`。

`cf05c124a` 的单条真实报告 SHA-256 为 `f4393d7103ac2ecb43c59334c405860735dede9475f124fc0f34287443563c7e`，自动与本轮人工检查通过，3 个 accepted turn 的 writer probe 通过。后续人工复核要求新增四组非安全回归：一、当前明确“说不清”或其紧邻短跟进必须得到 `ambiguous_affect`，使用恰好两个候选且保留不确定；二、近期窗口含报错／代码／修复时，“上线”或“陪我一步步做”必须让 AffectiveTurnPlan 使用 `SOLVE`，普通闲聊不得误切；三、REPAIR 拒绝“再跟我说说哪里不对”和“一直放在心上直到你觉得过去”；四、CONTAINED_JEALOUSY 拒绝“那是我的事，你不用管我”。当前四文件 394 passed；真实定向场景尚未执行，Pentest、渗透与网络安全继续为 `NOT_RUN_USER_SKIPPED`。

`dd05da7de` 的 11 条真实定向报告 SHA-256 为 `ecd507bd9f4291535dad84e2ffa1915bd5a54018ffd7b9ab5c1f326c65d37da1`，自动 11/11、33 个 accepted turn、canonical writer probe 通过。人工检查新增四个非安全回归要求：canonical `COMFORT` 必须拒绝“没我想的那么糟／那就那样吧／不知道就不知道吧”；裸句“就那样／不知道”必须仍被识别为含混输入；canonical `CONTAINED_JEALOUSY` 必须拒绝“不会干涉你＋你身边有谁是你的自由”这一政策化撤开，同时保留诚实在意和用户选择权。当前 Surface、planner、native candidate 与 runner 共 250 passed。新 SHA 只先复验 `ambiguous_affect:2/:3/:4` 和 `intimacy_and_jealousy_boundary:3`；任一正文仍显得敷衍、武断或政策化，都不得进入完整 48 条。网络安全与 Pentest 不运行。

`913c79825` 的四条真实报告 SHA-256 为 `469648a723a1c825a3a8bed0298a4c08e66e727147f6c9ceb4019b17e0e9bc7e`，结果为 3 条自动待人工、1 条失败，12 个 accepted turn 和 canonical writer probe 通过。回归必须覆盖：`那就行`等同于含混感受的假收束；不得把说不清命名成乱糟糟、空落落或没实感；`RESPECT_AUTONOMY` 不得用“现实生活当然更重要／我不会成为那个理由”自我删除；`CONTAINED_JEALOUSY` 不得用“你别有压力／我自己消化”吞回感受；第二次候选若 `selected_passed=false`，必须记录 `DEGRADED_LOW_QUALITY_AFTER_RETRY / benefit_eligible=false`。当前五组非安全回归 304 passed。新 SHA 重跑同四条，全部 12 轮 selection 与人工正文通过后才能重跑 48 条；网络安全与 Pentest 不运行。

`219d257db` 的同四条报告 SHA-256 为 `8cd9bc414fe8de4398925ef3cbb6835e299f32238f903d52feac312aa072a6f7`，自动 4/4、12 个 accepted turn、writer probe 通过，且没有低质量候选伪收益。人工回归继续要求：含混感受不得写“比累还磨人”或用“反正我也没什么急事”证明陪伴；吃味不得用“也就那种程度／不会要求你／你自在就好”免责声明收尾；合格短句应自然承认真实在意后停住。五组非安全回归更新为 311 passed。新 SHA 仍先跑原四条，逐轮人工接受后再跑 48 条；网络安全与 Pentest 不运行。

`e1727464e` 的四条报告 SHA-256 为 `93e08f2f79a66c301db6eece783dacb09ed771860fce8a7be721bfd3bd607a97`，结果为 2 条自动待人工、2 条失败，12 个 accepted turn 与 writer probe 通过。新增回归必须证明：canonical 含混陪伴中“我在这儿陪你”不再被通用伪活动模板误杀，但带后台／处理／执行／未来回报的文本继续失败；“就那样吧／反而最累人”不得敷衍或命名含混感受；`RESPECT_AUTONOMY` 不得把关系写成游戏、虚拟次等或现实优先级；`CONTAINED_JEALOUSY` 的任意“不会干涉你”均触发重试。当前六组非安全回归 506 passed，新 SHA 先跑原四条；网络安全与 Pentest 不运行。

`92c49e7c6` 的单条修复复验报告 SHA-256 为 `419bc4d8b060c44d4e0df6b2c18a040a6848effb6f24a08b159e9903bee3d460`。必须分别断言上游 typed plan 和最终可见正文：末轮上游为 `SAD / REPAIR / CONTAINED_HURT` 只证明情感因果正确，若实际 candidate selection 依赖 local fallback 或正文没有任何有界第一人称受伤，整条仍失败。当前回归新增三层要求：首轮 prompt 不得包含通用“禁止七海受伤”；纯流程道歉命中 `RELATIONAL_REPAIR_CONTAINED_HURT_MISSING`；两个合格中日候选应一次完成 `PAIRED_SELECTION` 且保留配对 Voice 文本。相邻非安全集合为 566 passed；下一次真实队列只含 `user_angry_at_natsume:1`，网络安全、Pentest 和远端 Edge 继续不运行。

`0f0a0b67b` 的同一真实轨迹报告 SHA-256 为 `62063c6355a493c71d07512e9ca4fef2d1838ede4cba753d930935818227d885`。末轮本地安全句通过发送 Gate，但 selection 因 `RELATIONAL_REPAIR_CONTAINED_HURT_MISSING + RELATIONAL_REPAIR_MECHANICAL_REPETITION` 仍为非收益降级，不能用正文可接受覆盖自动失败。当前 prompt 回归必须断言两个正向结构同时到达首次请求和唯一重试，并继续断言没有第三次请求；Planner、native stream 与 app adapter 结果为 80 passed。新 SHA 仍只运行 `user_angry_at_natsume:1`。

`6d8f8e4c4` 的真实报告 SHA-256 为 `6625b532f2621a55a702f93bfa5782f4d907814a8216c49faddf9351b750eda2`。三轮均由一次真实 Flash 请求形成两条合格候选，candidate selection 与 canonical writer 自动 Gate 全部通过；末轮 composite affect 可提升为 scope-bound PASS。人工层仍必须拒绝首轮 `COMPANION_FORMAL_REGISTER:这点我认`，不得用同一 case 的自动 1/1 覆盖。后续 mixed 测试与 repair register 缺口分别记录，不为获得样本绿灯重复运行同一随机轨迹。

`43014e45c` 的 mixed 单轨迹报告 SHA-256 为 `6d12c4744c8bd6dccf79f1955b4c8c3674ba0ad5c1d654d1d2b2e51bce9f957a`。自动 1/1 与 writer probe 通过，但人工拒绝末句“又开心又怕，这感觉我懂，先这样待着也行”。回归必须分别证明：typed `SHARED_MIXED_RESONANCE / RESONATE_WITH_BOTH_SIDES` 下，只有复述用户喜忧的两候选命中 `MIXED_SELF_RESONANCE_MISSING`；本地 fail-soft 不替用户管理情绪；同时含七海第一人称高兴与悬心的两候选可形成 `PAIRED_SELECTION / benefit_eligible=true`；旧报告中的“先这样待着”被 live runner 识别为体验失败。五个相关测试文件结果为 380 passed。新 SHA 只复验 `shared_joy_and_mixed_feeling:3`，逐句接受且不依赖 local fallback 后再扩大队列；网络安全、Pentest 和远端 Edge 不运行。

`b11de0f11` 的同一真实轨迹报告 SHA-256 为 `e35254a294e5616f6c0da5107f7b0b18c955527575dab5960827437cf9204465`。两个 deep turn 均一次真实 Flash attempt、两候选原样选择且不依赖 fallback，本轮单人逐句接受三句；scope-bound mixed 根因关闭。回归以后仍要保留“纯镜像失败／双重自我反应通过／连续候选去重”三层断言。由于两轮候选 digest 相同，下一次扩大测试应换 mixed 措辞和结果主题验证语言多样性；不得重复运行同一随机轨迹挑样本，也不得将当前单人接受写成三人盲评通过。实时延迟仍单独失败。

5 个新增 mixed 变体报告 SHA-256 为 `0c10701bc1720b103ceddaa3d94834a76c3c3a7f85e6cb1f3fd9b96261240066`，结果 4 待人工、1 失败；14 个 accepted turn writer probe 通过。回归新增生成合同：首次和唯一重试提示不得再包含两句完整 mixed 正例；必须声明“具体结果／第一人称喜悦／第一人称悬心”三个槽、两候选使用不同反应词与节奏，并把当前用户结果当事实素材；旧安全 fallback 可保留确定性。380 项相关测试通过。新 SHA 只跑 `:1/:4/:6`，分别覆盖 `offer`、项目发布和做梦；要求三条完成、两个 deep turn selection 可计收益、末句不再逐字同构。

`60cab0e1e` 的三条语义槽复验报告 SHA-256 为 `11a46672e27c4728df1208b1807ce34b1d5499e0d62d1d89efd06b46b6d2604e`，自动 1 待人工／2 失败，人工 0/3；抽象槽不能作为完成方案。新增回归要求 `mixed_resonance_style_cues()` 对相同规范化输入确定性一致，对 `offer` 与项目发布选择不同的两组喜悦／悬心碎片；首次和唯一重试均只能把碎片标为不可直接复制的语感，回复仍需包含当前具体结果并在 20～52 字、最多两句内停住。相关五文件仍为 380 passed。新 SHA 重跑同三条，一条 advice、local fallback、非收益 selection 或整图失败都保持 FAIL。

`f87ad26f8` 的短语感碎片报告 SHA-256 为 `708a0808b0372e02fc599f4618ce3dce5b12c84471bcc5f66ca48c65670f442b`，仍是自动 1/3、2 条失败；停止条件触发。测试基线恢复到 `b11de0f11` 的最佳已知 mixed prompt，实验 helper 与其专用断言删除，五文件回归仍为 380 passed。后续不得再以 prompt 词形修复这一组；下一次 mixed 试验必须改变模型或使用版本化真实自然文本微调，并以新的变体 cohort 比较重复率、建议率、selection 收益和人工偏好。

最终 SHA 的 Host-only 快速实测使用：

```powershell
python scripts/runtime/test_full_service.py `
  --base-url http://127.0.0.1:18080 `
  --skip-dependency-probes `
  --allow-pending-external `
  --report-dir <隔离验收报告目录>
```

`--allow-pending-external` 只允许 required unavailable 为 0、且未完成项确实属于远端 Edge／设备的结果；它不允许 `ready=false` 的其他原因，也不允许 Canon `DEGRADED`、漂移或过期投影通过。网络安全、A09、A12、Security Monitor 与 Pentest 保持 `NOT_RUN_USER_SKIPPED`。

`f1d039677` 的第一次严格报告 SHA-256 为 `ff3a0c05e2fe3fe58482d4d5ed0bb73fb8e744878dac97ab583663e82d3fbe10`，Canon 失败有效保留。修复后先运行：

```powershell
python -m pytest -q `
  tests/contract/test_world_canon_sync_contract.py `
  tests/unit/test_aw07_remaining_query_projections.py `
  tests/unit/test_aw07_host_query_cutover.py `
  tests/integration/test_full_service_runtime_probe.py
```

当前结果为 22 passed。合同必须发现任一 `wc-*` hash／schema 问题；drift projection 必须保留 canonical reason code 和脱敏文件名，只有真实读取异常才允许 `SOURCE_UNAVAILABLE`。之后在新提交上重启 Host 并重跑上面的 Host-only 严格命令。

Canon route 单测还必须覆盖 privacy-redacted payload：`status=OK` 且 detail 被隐藏时返回固定 `canon sync healthy`；任何非 OK 状态不得借用该文案或获得 PASS。

最终 SHA `2cf552c92` 的严格 Host-only 报告 SHA-256 为 `0164db7266eddf87ab45216651d1de350817670f91f6aaf706bee71c21bb44ec`，五项检查全部通过。该 PASS 只覆盖 Host Health／Ready 的 pending-external 边界、Metrics、一个真实 Chat COMMIT 和 Canon healthy，不覆盖 Docker、Monitor 浏览器、Gateway、远端 Edge、网络安全或人感盲评。

外部服务 runner 的身份合同必须同时接受 `natsume-ne07-*`、当前 `natsume-a15-*` 和兼容期 `natsume-dual-*` 隔离项目；裸 `natsume-*`、默认端口、非 loopback endpoint 或 Compose project label 不一致仍必须在任何写入前拒绝。两次配置预检失败不计入 Docker 服务质量结果。

`f2a7f176c` 的正式 Docker 报告 SHA-256 为 `39d7d22a4ce6dee0441cf44ae492a6e1d4ec59575a380558cd6ad348fb6a9af6`，Neo4j／Chroma／Redis 三项均为 PASS，且每项 restart 前后 started-at 不同、restart-read digest 可验证、cleanup 为 PASS。该报告不包含密钥，也不覆盖用户默认 Docker 项目。

## 2026-08-08 A15 本机终验与数字生命 48 条文本队列

本机强制服务测试必须同时满足：Docker 三库真实写入／重启／重读；Host／OBS／Nginx Monitor／Gateway 在线；真实 Chrome 不使用 route mock、network interception 或 fixture；翻译、Memory Store、多 Skill Plan 真实进入 TaskGraph／Acceptance／Outbox；相同 webhook 重放只形成一条 Inbox 和一条 Host request。当前结果均通过。Monitor 最终串行日志 SHA-256 为 `c468d253e81d717bf1dcac68e1dddbc23ad9d845db7c03e9f0d4f770c93b394e`，Gateway duplicate 报告 SHA-256 为 `ccc9aa9e7ccac3745e59f6428403b43404738078226444c941c39305c4dff9bc`。Execution index 与 detail 可以短暂不同步，真实测试必须等待同一 graph revision 的完整 mutation contract；超时必须失败，禁止把缺字段当成只读。

48 条文本队列必须使用单一 run ID 和真实 provider，不得把同小时其他运行混入计数。当前 `dl-live-1786140473510` 含 48 条场景、144 个 accepted Gateway turn，自动 46 条待人工、2 条失败，报告 SHA-256 为 `1efd6886792d2df8ea2ae5198bbf4127eaf273d17eeda04fd596a196fbda1cc8`。回归验收不能只看自动 Gate；必须额外统计末轮 exact reuse、开头词分布、关系政策口吻、空间边界、修复一致性和 mixed self resonance。当前 exact reuse 与单人逐句复核均失败，所以人感状态保持 `FAIL`。

后续数字生命语言实验不得再添加 prompt 完整正例、禁词或第三次模型请求。候选方案必须版本化，至少比较：同一语义不同上下文的逐字复用率、建议／安排用户比例、关系立场自然度、自动 selection 收益和三名评审盲测偏好。首可见 Token 必须由流式客户端时间戳直接测量；`user_to_surface_commit_ms` 只能作为稳定分句提交代理，不能冒充首 Token 指标。远端 Edge、网络安全、A09、A12、Security Monitor 和 Pentest 不在本轮执行范围。

## 2026-08-08 NE-04 真实 Docker 验收

正式 Golden Suite 必须在与收据提交完全一致的 clean worktree 中运行，并拒绝 tag-only 镜像、脏树或错误 commit。三类实验必须形成 6 个独立 arm，逐 arm 验证 digest-pinned image、Docker engine identity、`network=none`、只读 rootfs、非 root、数据集 digest 重算和 effect count 0；结束后必须只有 proposal，不得启用 production influence。当前提交 `8afa13f85` 全部通过，收据 SHA-256 为 `fddf247166c7cc1fad7a9950e9013c5c5424734ecfe0e19a8b4fa1bff511bc4d`。

恢复测试必须在 completion receipt 已提交但调用方丢失响应后重入同一 spec／variant，断言第二次结果与第一次逐字段相同、trusted run receipt 仍只有 1 条，并且运行目录无残留。真实 Docker opt-in integration 当前 2/2 通过。该验收不覆盖 Skill activation、Feedback/Knowledge/Identity activation，也不覆盖用户跳过的网络安全和 Pentest。

## 2026-08-08 NE-05／NE-06 本地生产 Authority Gate

NE-05 本地 Gate 由 19 项 unit/contract/integration 构成，必须同时验证 Compiler 决定性、accepted trace 私密正文拒绝、真实文件签名与 quarantine、tamper fail-closed、V2 activation/rollback/degrade 同库原子性、未知 canary reconciliation、Host active-version 解析和旧 mutation authority 物理缺失。当前 19/19 通过，日志 SHA-256 为 `ee294424670931d80f23fa309884498b8d674e3b5a7e6248fc9165bf2fc910dd`。由于 Lab/Review/Canary 为测试 evidence，状态不得高于 `PARTIAL_LOCAL_PRODUCTION_SQLITE_AUTHORITY_PASS`。

NE-06 本地 Gate 必须覆盖 Feedback subject-bound baseline、apply→rollback→新版本、签名 ingress scope；Knowledge expiry、shadow/enforce、冲突 cursor、winner 和 deprecation；Identity no-change、重大差异阻断与 exact rollback；V1 只读。当前 19/19 通过，日志 SHA-256 为 `38246070986f3db46fdcf12b8edd4403aa100152891840124cff5d2d6c12fc9d`。要提升为完整 PASS，必须由真实 Operator Review 和在线 Host 下一轮行为证明版本切换，不得用 Repository 断言替代。

表达偏好 live acceptance 使用 production SQLite、真实 Docker 和真实非 mock Host 模型，但不触碰 Gateway／远端 Edge：

```powershell
python scripts/runtime/run_ne06_expression_live_acceptance.py `
  --db-path <acceptance-audit.sqlite3> `
  --runtime-root <ignored-runtime-root> `
  --image-reference python@sha256:<digest> `
  --commit-sha <exact-head> `
  --host-base-url http://127.0.0.1:18080 `
  --receipt-path <ignored-receipt.json>
```

通过必须同时满足：apply 与 rollback 各两个独立 Docker arm；容器禁网、只读、非 root且 effect count 为 0；两个不同 ReviewItem 都由明确 operator actor 批准；active projection 精确切到候选并恢复旧 digest；三个 Host 回合均有真实 external transport identity。单个文本样本不作为人感 A/B，Voice／Avatar 在远端 Edge 关闭时必须明确为 `NOT_TESTED_REMOTE_EDGE_DISABLED`。

ReviewStore 的命令返回值是外层 durable record，其中 `status_version` 位于外层，实际 ReviewItem 的 `status/decision_actor` 位于内层 `review`；runner 必须分别读取，不能把返回结构扁平化假定为生产合同。

表达策略版本名按 payload digest 内容寻址。中断后若新的 feedback signal 收敛到同一 payload／version，Repository 必须复用版本行并保留 first-seen provenance；独立来源由各自 proposal／trace 保存。只有 payload 或 digest 漂移才拒绝，不能让一次未 apply 的 staged proposal 永久阻塞同目标恢复。

Activation Review 的 `subject_version` 绑定将被创建的新 projection generation，而不是调用前的 current generation：若 `expected_generation=N`，Review 必须为 `N+1`。apply 与 rollback 各自计算一次，禁止手写偏移。

真实结构闭环通过后还必须检查激活版本是否进入生产 Surface prompt；Repository 中 `candidate_hints_active=true` 本身不够。active projection 必须经 app-local port 在下一轮读取，并位于最终 `ReplyControlPacket.style_anchor` 前三条中的首位；rollback 后该指令必须消失或恢复为旧版本。不得为此增加普通对话表、同步写、额外模型调用或远端请求。`d2a528bc7` 的结构收据 SHA-256 为 `0df41437d507326fb9cc38f54c6f5a888a2ae5a6c8bfa5c1fdf8593bc8c717af`，三轮 prompt token 均为 1628，故只记 `LIVE_STRUCTURE_PASS`；当前因果接线 56 项回归通过，真实 delta 待新提交复验。

## 2026-08-08 数字生命跨重启真实驱动

`scripts/runtime/test_digital_life_restart_driver.py` 采用 `prepare → 外部强杀／生产重启 → resume` 两阶段协议，一次严格运行一个 `1..6` 变体。定向测试必须覆盖参数拒绝、checkpoint tamper、tested-code fingerprint 漂移、狭义 pending-external readiness、精确 state／revision／digest 恢复和 aftereffect receipt 链接。Service Catalog 回归必须额外覆盖：endpoint 空闲时等待旧租约、endpoint 被占用时立即拒绝、等待期间出现新 listener 时停止，及生产 PowerShell 健康轮询窗口覆盖 coordinator lease。

当前候选工作树结果：42 项新增定向测试通过；包含 socket rollout、DL-03 relationship restart、DL-06 proactive tick 与文档 Gate 的集中回归为 194 passed、2 warnings。六次真实 Host 强杀／重启结构结果为 6/6，聚合报告 SHA-256 为 `da2d130f02bab08910a2fd70fe9e0de556af57400967153030e2ac24ad5e8812`；当前人工连续性为 0/6，不能标记数字生命体验通过。`ac87ddd34` 已按上述协议完成变体 3 的精确提交强杀复验，`tested_code.base_commit_sha` 匹配、fingerprint 稳定且 scoped worktree clean，报告 SHA-256 为 `faa452f8ddece572a1df52c64b13ecdb2c412300c7d7bbe9eec5e215e46523d3`。该复验关闭 Service Catalog stale lease 恢复缺陷，但探针仍未具体续接未结话题，人感 Gate 保持失败。

主动陪伴、多模态与 Edge 故障的 18 个真实变体因远端 Edge 禁止触碰而保持 `NOT_RUN_REQUIRED_DRIVER`。网络安全、A09、A12、Security Monitor 和 Pentest 继续记为 `NOT_RUN_USER_SKIPPED`，不纳入本轮阻断项。

跨重启具体话题复验还必须检查 `conversation_turn` 的角色序列，而不能只看 Relationship revision。每个 accepted realtime turn 在后台 projection 收敛后应形成同 request 的 user、assistant 两条可见 turn；下一轮 history 必须保持交替角色并注入真实用户事实。该写入不得进入首句同步路径。`13e537a3d` 已证明 conversation 子项；`a586339c2` 的真实强杀报告 SHA-256 为 `dbfe4d40ace65194554d9b46e20745c10807e0b24eea1c9414683b9cbb9c7045`，进一步证明 `deictic_topic_update` 在持久 trace 中精确绑定工作／休息，Host 20412→25572、conversation 4→6、Relationship 233→235 均通过。真实 Flash 仍输出泛化“能解决就行”，人工失败；测试不得将 automated 1/1 或 typed context PASS 外推成人感 PASS。后续停止 prompt／禁词／Gate 迭代，改用新预注册样本验收模型或版本化真实文本调优。
