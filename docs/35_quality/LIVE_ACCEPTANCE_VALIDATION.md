受众：CODEX
标题：真实服务与陪伴体验验收

# 真实服务与陪伴体验验收

真实验收只在代码波次结束后集中执行，使用隔离 acceptance 数据根，不写用户现有 Memory、Persona、Gateway 会话或 Docker namespace。

## 本地强制服务

1. 启动固定版本 Neo4j、Chroma、Redis，并完成真实写入、读取和重启读取。
2. 构建 Monitor UI，启动 Host、OBS、Nginx Monitor 和本地 Gateway。
3. 使用至少一个真实非 mock provider 完成普通 Chat 与一条有副作用路线。
4. 核对 Life journal、Task ledger、Acceptance、Outbox、Gateway receipt 和 Monitor projection。
5. 注入 Host 崩溃、网络中断、重复入站和未知副作用。

## 目标 Edge

Edge 位于另一台电脑，只有用户明确允许后才启动。实测中文 ASR、日语 GPT-SoVITS V4、VTS/VRM、打断、重连和三模态同步。缺少物理 receipt 不能把 Host outbox 记作设备成功。

## 体验

固定场景覆盖低落、冲突、修复、关系边界、久别、混合情绪、主动消息被忽略和技术协作。至少 3 名评审完成 48 条多轮盲测，并满足 PRD 指标。

## 证据

证据记录 commit、配置 digest、真实服务身份、provider/model、usage、延迟、故障与恢复结果。输出位于忽略的 `artifacts/local-gates/<sha>/`，不包含密钥、用户正文和原始音视频。

fixture、mock、scripted replay、静态端口探针和历史 receipt 不能替代本验收。

## 2026-08-03 双端 P0 验收事实

本轮在隔离 Host 数据根和远端 Edge 运行根完成以下真实验证，证据保存在忽略目录且不包含账号、消息正文或密钥：

- 真实消息平台入站经 Gateway Secure Ingress 进入唯一 HostCommand／TaskGraph；图、节点、Attempt、Budget、Verification 与 Acceptance 均达到终态。
- Acceptance 原子产生 Gateway delivery intent，真实平台 outbox 达到 `ACKED`；重复入站复用原有 command／graph，未重复建图或外发。
- Edge 的 ASR、GPT-SoVITS TTS、音频播放、Edge RPC 和 Unified Gateway 为 `UP`；VTS 已认证但缺兼容情绪热键而为 `DEGRADED`，barge-in 关闭并为 `NOT_TESTED`。
- 两段真实 TTS effect 在物理播放完成后回写 `APPLIED`；两个缺失热键的 VTS effect 如实回写 `REJECTED`，Host 与 Edge durable receipt 一致。
- OBS 的 15 项服务投影为 13 `UP`、1 `DEGRADED`、1 `NOT_TESTED`、0 `UNKNOWN`。
- Nginx 托管的真实 Monitor 使用当前 Host／OBS 数据完成无 fixture、无网络拦截的 Playwright 路由与交互验证。
- 提交 `cdaa4ea54` 绑定的外部服务回执为 `PASS`：固定版本 Neo4j、Chroma、Redis 均完成真实写入、读取、容器重启和持久读取；脱敏回执 digest 为 `sha256:282cb3f08b2896500bfad175121f30804bfe646d7697c9d26669b315326b33af`。
- 容器重启后 Host health／ready、OBS、Nginx Monitor、Edge health 和 Gateway health 均返回 200。

Gateway 的公开 `/v1/gateway/stats` 仍按合同返回 `GW_ADMIN_AUTH_UNAVAILABLE`，因为未安装可认证的 Gateway 管理面；这不是 Monitor 的数据源。Monitor 使用 Host/OBS 从 durable Gateway transport ledger 派生的 `/obs/gateway/stats`，真实后端 E2E 已覆盖该路径。公开 health 不能替代平台细节，平台 observation 缺失时页面保持“未确认”。

这些事实证明本轮 Gateway、canonical runtime、具身回执和健康投影 P0 闭环可用，不代表完整数字生命发布通过。VTS 情绪热键、barge-in、48 条多轮盲测和完整实时性能门槛仍待完成，整体继续为 `INCOMPLETE`／局部 `ACCEPTANCE_PENDING_EXTERNAL`。

## 2026-08-03 两轮实测问题收口

第二轮复核确认，“待连接”不是 Host、OBS 或 Nginx 未启动，而是普通浏览器没有 operator session；旧 E2E 通过是因为测试上下文直接注入了原始 Token。现已改为：

- `start_host_side.ps1` 仅在 loopback Monitor 上生成或复用本机 owner token，文件 ACL 只授予当前 Windows 用户；公开运行环境只保存 token digest 和文件路径。
- 正常浏览器同源 POST `/obs/operator-session/bootstrap`，OBS 校验 loopback、Origin、`Sec-Fetch-Site`、digest authority 和 `obs:operator:read` 后只设置 HttpOnly、SameSite=Strict Cookie，不向 JavaScript、URL、日志或仓库返回 Token。
- HTTP 中间件只豁免精确的 bootstrap POST；同路径 GET 和其他 OBS 查询仍需授权。WebSocket 继续复用同一 Cookie authority。
- 在隔离验收根重启真实 Host、OBS 和 Nginx 后，无预置 Cookie 的 Chrome 已通过 bootstrap、`/obs/runtime-snapshot` 200 和 `/obs/ws/health` 真实快照验证。

第一轮 6 个前端失败也已按真实架构纠正：Security Hunt、Tool Execute、MateEngine Action/Say 的旧 command 已退役，页面不再保留会必然返回 `OPERATOR_COMMAND_RETIRED` 的可点击写入口；Gateway 文案测试同步当前 `unknown` 字段合同。Cockpit 将时钟、演化、Runtime、Capability 与 Gateway 深层诊断默认折叠，1440×1000 实测页面高度从约 8486px 降至约 5150px，主要交互目标统一不低于 24px。

会话与退役入口切片的定向证据为 Python 92 项、前端 read-model 22 项、Monitor 交互回归 23 项、正常浏览器真实会话 1 项。驾驶舱实时投影修复后追加通过：OBS FastAPI 生命周期 16 项，Runtime／Summary／Life Body／Trace／Repository 聚焦回归 12 项，前端 read-model 22 项，Monitor 交互回归增至 24 项，真实 Nginx／OBS 普通浏览器 1 项。`brace-expansion` high 已通过锁文件 override 消除；在线 `npm audit` 仍报告 React Router 2 项 moderate、0 high、0 critical。当前公布的升级目标会引入一项更新的 high advisory，且 Monitor 是 loopback-only CSR HashRouter、未使用 SSR hydration，因此暂不做不安全的强制 major 升级；该项保持显式依赖边界，不冒充清零。

以上是两轮实测问题的局部闭环，不改变 VTS 热键、barge-in、多轮盲测和实时性能仍未完成的结论。

## 2026-08-03 驾驶舱实时投影恢复

普通浏览器会话闭环后，驾驶舱仍出现“待连接／Runtime 待恢复”。这不是 Edge 连接状态，也不是 Host Runtime 缺失：同一时刻 `/obs/runtime-snapshot` 为 `HEALTHY`，但 `/obs/life-body` 因串行投影波次中的历史 Trace 重建耗时而返回空聚合。真实 acceptance 库中 Trace 刷新会对数百个历史 request 逐条执行空的 stream 查询，并在没有来源变化时重复写 freshness；随后 Insights 读取的 15 秒 Agent Loop 投影已经过期，最终连带 Life Body 降级。

本轮按实时显示优先级完成以下纠正：

- Cockpit 并行读取 canonical `/obs/runtime-snapshot`；Life Body 聚合重建时仍显示真实 Runtime，不再把聚合缺失解释成生命体未连接。
- Projection daemon 先生成 Runtime、Digital Life Overview、Insights 和 Life Body，再处理历史 Trace；Trace Feed 作为诊断增强源，缺失时只降级 Trace 明细。
- Trace stream 先用 SQL 聚合一次读取每个 request 的最新 cursor，不加载全部事件正文；未变化的历史 request 不再重建、不再刷新写入，只有发生变化且进入本波有界批次的 request 才读取明细。
- Insights 的 Agent Loop 依赖采用与聚合一致的 freshness，不再使用面向直接路由的 15 秒 TTL；60／360 分钟聚合保留一个完整真实投影波次的 180 秒窗口，而 canonical Runtime 仍维持独立的 45 秒实时窗口。
- 健康投影的 `reason_code` 为空，不再同时返回 `available=true` 与 `OPERATOR_PROJECTION_NOT_READY`。

加载修复代码并重启真实 Host、OBS 与 Nginx Monitor 后，projection daemon 波次达到 22/22 成功；SQLite 中 `digital_life_runtime_snapshot`、`digital_life_overview_snapshot`、`insights_summary_snapshot` 和 `life_body_snapshot` 均为 `HEALTHY`。无预置 Cookie 的普通 Chrome 已验证：页面无“待连接”、无“Runtime 待恢复”，`/obs/life-body` 为 `available=true` 且包含真实 Runtime Snapshot，HTTP 与 WebSocket 仍使用同一 operator session。远端 Edge 未因本修复启动或重启。

这是 Monitor 实时投影缺陷的闭环，不改变完整数字生命验收仍为 `INCOMPLETE` 的结论。

## 2026-08-03 能力投影与数字生命主链复核

再次以真实 Nginx 页面复核时发现两个后续问题：顶部直接展示 Capability Forge 503 的 JSON；数字生命主链仍只消费旧 soak 摘要，显示“主链存在断点、3/8、idle tick --”。根因不是远端 Edge，而是 Host→OBS 只读凭据未形成可重启的文件 authority、能力读模型 15 秒有效期短于完整串行投影波次，以及慢 Task Runtime 回调在 lease 到期后饿死排在后面的 Life Body 聚合。

本轮完成以下收口：

- `start_host_side.ps1` 在隔离 RuntimeRoot 生成／复用 ACL 保护的 Host→OBS Token 文件；公开 runtime env 只保留文件路径，Host route 与 Obs transport 同时支持文件读取和旧环境变量兼容。
- Host 五条只读 source 的默认超时调整为 5 秒，并在全部 provider 返回后封装响应时钟；明确的未来边界字段可以晚于 observed time，普通 evidence timestamp 仍 fail-closed。
- Capability Forge 继续按 15 秒尽力刷新，但读取 freshness 调整为 120 秒，覆盖一轮真实 22-job 串行投影；前端即使收到失败也只展示可读 reason，不再把原始 JSON 暴露到横幅。
- Projection daemon 在无竞争 leader 时可以重新取得过期租约，且 Life Body／Overview／Insights 排在独立慢 Task Runtime 与 Trace 之前。`life_body_snapshot` 直接携带当前 `organ_tribe_status`；主链使用 23 个实时 Agent 和 Host scheduler clock，不再依赖旧 soak 推断。

真实重启 Host、OBS 与 Nginx 后，Chroma、Neo4j、Redis 探针均为 ready；SQLite 中 `capability_forge_operator`、`organ_tribe_status`、`life_body_snapshot` 均为 `HEALTHY`。无预置 Cookie 的真实 Chrome 验证 `/obs/life-body` 返回 23 个 Agent、`clock_active=true`、`last_tick_ms>0`，页面不存在原始 `reason_code`、能力投影不可用横幅、“主链存在断点”或空后台 tick。远端 Edge 未启动或重启。

定向回归覆盖 Python Host source／投影／Life Body／启动脚本、Monitor 27 项 read-model、生产构建和真实 Nginx Playwright。该结论只关闭截图中的两个 Monitor 问题；人格上下文样本、门控告警、VTS 热键、barge-in、多轮盲测和实时性能仍按各自状态处理，整体保持 `INCOMPLETE`。

## 2026-08-04 真实服务续测与 VTS 断线恢复

在上一轮服务未替换为 mock 的前提下继续验收：固定版本 Neo4j、Chroma、Redis 再次完成真实写入、读取、容器重启、持久读取与 namespace 清理；提交 `be4e6d428` 对应脱敏回执 digest 为 `sha256:e61dca891d8d2a04409c443f0df682439e6c06695e10f3b06e089058f37365fe`。容器重启后 Host health／ready、OBS 和 Nginx Monitor 均恢复。真实 Nginx／OBS 浏览器路由与会话共 63 项通过，完整交互以单 worker 重跑 24 项通过；并行首轮出现的单个 Chrome `Page crashed` 隔离重跑通过，判定为浏览器资源抖动而非业务断言失败。后端真实 roundtrip、projection rebuild、Gateway transport 与 Host／Edge 合同集成 41 项通过。

远端续测发现 VTS 顶层曾显示 `AUTHENTICATED`，但生命动作线程仍保留陈旧的 `CONNECTING`，并对已经收到 WebSocket `1001 going away` 的失效连接持续发帧，累计约 7.9 万次错误。修复后：

- `1001 going away` 被识别为可恢复连接关闭；一次同步恢复失败后转交现有后台有界退避，不再按动作帧频率刷错。
- 生命动作等待态会对 `ERROR／DISCONNECTED` 请求一次后台重连；`RECONNECTING` 期间不重复启动线程，恢复后的 health 使用共享 VTS driver 的当前状态。
- 真实故障注入中强制结束 VTube Studio，Edge 未重启；VTS 再启动后，Edge 经 6 次退避自行恢复为 `AUTHENTICATED`，重连计数增加 1。恢复后 20 秒内动作帧增加 68，动作错误和 model-move 错误增量均为 0，ASR 与 GPT-SoVITS 继续为 `READY`。
- 恢复后通过真实 HMAC Edge RPC 合成并播放一条日语 GPT-SoVITS 语音：请求与播放启动均为 `ACCEPTED`，物理播放完成且无 playback error；VTS 口型同步完成 26 帧、0 错误，同期生命动作增加 19 帧、0 错误。expression hotkey 仍如实为不可用。

Edge 总体仍为 `DEGRADED`，原因只剩当前模型没有兼容的情绪 expression hotkey；barge-in 也仍关闭。此次只证明 VTS 传输断线恢复和状态真实性，不把表情热键、三模态同步、盲测或完整实时性能提升为通过。

## 2026-08-04 Gateway 真实探针纠偏

继续使用隔离 Host 数据库与远端 Gateway 做真实请求时，原 `test_gateway_live_matrix.py` 暴露出两项测试基础设施漂移和一项生产路由缺陷：探针仍发送安全加固前的未签名 webhook、仍读取已退役的 `graph_run_trace`；真实翻译请求在 Thalamus 注入 `pragmatic_frame`／`conversation_understanding` 后，又被 `work_translate` 的旧参数白名单以 `INTENT_ARGS_INVALID` 拒绝。

本切片将探针改为独立计算 Gateway HMAC，密钥只从指定运行时环境变量读取且不进入报告；图证据优先读取 `host_command_journal`、`task_runtime_graph` 与 `task_runtime_node`，仅对历史数据库保留只读 fallback。`work_code`／`work_translate` 只新增三个由 Host 构造的 typed context 参数，未知扩展仍 fail-closed。定向复验为 19 passed。

首轮真实失败证据被保留：webhook 以 200 写入 Durable inbox，但隔离 Host 与远端 Gateway 使用了不同代密钥，Host 连续八次在 Credential Gate 拒绝，inbox 最终进入 DLQ。对齐密钥后同一路线在约 2 秒内完成 Secure Ingress `ALLOW`，随后准确暴露上述 `INTENT_ARGS_INVALID`，证明探针不是端口占位。完整 Gateway→TaskGraph→模型→Edge 终态仍须在加载本切片后重跑，因此本节状态为 `RETEST_REQUIRED`，不提升总体结论。

加载修复后的下一次真实翻译请求再次通过签名 webhook、Durable inbox 和 Governor `APPROVE`，但在 admission 前以 `GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE` 失败：Thalamus 将翻译交给 `translate_agent`，生产 Governed Delegate Dispatcher 却只登记了 `pentest_agent`。该失败证明翻译路由和执行能力之间存在真实断链，当前状态为 `FAIL`，不能因入口已通过而算作完整链路。

同轮运行还发现两个测试运行问题：远端 resident ASR 持续产生低置信噪声请求时，Host 启动稳定性检查两次失败；改用保留 RPC／TTS／VTS、关闭常驻麦克风采集的 Gateway voice ASR 模式后，Host、OBS、Monitor、Edge 与 Gateway 才同时稳定可达，因此常驻 ASR 噪声门控仍是未关闭缺陷。`stop_edge_side.ps1` 还把命名参数拼成字符串数组，导致 `PidFile` 被错误绑定到整数 `Port`；现已改用 PowerShell hashtable splatting。Gateway live matrix 现在读取 `INPUT_COMPLETED`／`INPUT_FAILED` 任一终态并在失败时立即保留 `reason_code`／detail，同时在终态后刷新 Host 日志，不再对已经明确失败的请求空等 180 秒。脚本与探针定向回归 36 passed；这些修改只提高诊断与启停可靠性，不把翻译或 ASR 生产缺陷标记为通过。

翻译断链的代码修复现已接入生产 Composition：`translate_agent` 作为低风险 `MODEL_BACKED_READ_ONLY` target 注册，直接使用现有 `ModelAttemptService` 和 `workbench_translate` prompt semantic；模型 guard 精确绑定 Task graph/node/attempt 的剩余预算，provider usage 回写 worker budget。它不持有工具、动态规划、嵌套 Delegate 或 delivery authority，普通翻译使用 Governor 低风险 waiver，不进入人工 Review。Pentest 原有 port-free、精确零用量和执行前 Review 保持不变。Delegate／Host／Input 相邻回归合计 158 passed；真实 Gateway→Provider→Acceptance→Edge 重跑前，状态只从 `FAIL_DELEGATE_TARGET_UNAVAILABLE` 提升为 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

## 2026-08-04 Gateway Reply Outbox 与 Life Body 真实复验

修复代码加载到隔离 Host 后，使用远端 Unified Gateway、生产 HMAC、真实模型路由和同一真实服务栈重新提交三条新请求：

| 场景 | 实际路由 | 结果 |
|---|---|---|
| `translate_delegate` | `translate_agent` | `INPUT_COMPLETED`，Acceptance Reply Outbox 已提交 |
| `memory_store` | `memory_store` | `INPUT_COMPLETED`，Acceptance Reply Outbox 已提交 |
| `multi_skill_plan` | `agent_frontend_review`、`agent_frontend_acceptance`、`planning_search` | `INPUT_COMPLETED`，Acceptance Reply Outbox 已提交 |

三条完成事件均携带精确安全枚举 `gateway_delivery_authority=acceptance_effect_outbox` 与 `gateway_delivery_state=OUTBOX_COMMITTED`。最新 30 分钟有效桶样本数为 3，Reply Commit、Closed Loop、Completion 均为 100%，告警为空。360 分钟窗口仍保留此前失败／不完整探针，因此历史汇总闭环率没有被重写为 100%。报告位于忽略的隔离验收目录 `artifacts/gateway-live/after-loop-metric-fix/`，不含密钥。

同次真实 SQLite 检查确认 `digital_life_runtime_snapshot` 的当前结构以 `hard_state.emotion_tag` 保存 canonical self affect。Sidecar 重启后 `/obs/life-body` 为 `HEALTHY` 且 `derived.currentEmotion=NEUTRAL`；真实 Nginx Monitor Playwright 同时验证页面无“待连接”、“Runtime 待恢复”、Capability Forge 原始错误横幅或“主链存在断点”，并显示人格上下文“情绪 平静”。

本地回归证据为：受影响 Python 30 passed，Monitor read-model 27 passed，Vite 生产构建通过，真实 `operator-session-live` 与 `core-release-real-interaction` 各 1 passed。这里的 Outbox commit 只证明 Host 已接受并持久提交 Gateway 回复，不能冒充外部平台物理 ACK。VTS 情绪热键、barge-in、常驻 ASR 噪声、长期盲测、PlanningSearch 三域收益和最终 commit 全栈故障矩阵仍未完成，整体保持 `INCOMPLETE`。

## 2026-08-05 A01～A14 前置门禁

在最新 `dev` 基线上纠正 migration head 到 `0153`，并对 A03 的 15 个大文件热点执行实体拆分。当前工作树静态 A01～A14 全部通过；Surface、Digital Life、Task Acceptance、Embodiment、TTS、Tribe 和持久化受影响波次回归 306 项通过，pytest 全量收集通过。

这不是 A15：尚未以提交后的最终 SHA 重启 Docker、Host、OBS、Monitor、Gateway 和可用 Edge，也没有重跑 PlanningSearch 三域收益、72 场景、三人盲测或完整故障矩阵。因此本节证据只能标记为 `LOCAL_STATIC_PRECONDITION_PASS`，总体仍为 `INCOMPLETE`。

## 2026-08-05 A15 当前 SHA 首轮真实全栈

在 `d7e0cfba8` 上已用隔离 acceptance 数据根启动固定版本 Neo4j、Chroma、Redis、Host、OBS、Nginx Monitor、远端 Edge 与 Unified Gateway。三项 Docker 服务均完成真实写入、查询、容器重启、重启后读取和 namespace 清理；Monitor 的真实 Nginx／OBS 自动化已证明不再显示“待连接”或“Runtime 待恢复”。远端 GPT-SoVITS 方案 B 完成日语合成、物理播放和 VTS 口型帧发送，但 VTube Studio 当前没有加载角色模型，因此不能把口型帧提升为可见 Avatar 动作通过。

Gateway 五路线首轮结果为 2 `PASS`、2 `WARN`、1 `FAIL`。失败路线真实生成并完成了三节点 PlanningSearch TaskGraph，三个节点均进入 `ACCEPTED`，但终局 Surface 只返回 24 字空泛短句，没有消费两个前序验收结果。该问题已定位为终局合成质量门缺失；修复限定在含 canonical accepted Task artifacts 的合成回合，普通实时聊天不增加检查或等待。代码加入领域无关的最小内容与多结果覆盖检查，并在模型仍失败时确定性保留全部已验收结果；真实 Gateway 重测前，PlanningSearch 路线状态为 `CODE_REMEDIATED_REAL_RETEST_PENDING`，A15 总体仍为 `INCOMPLETE`。

同轮还确认两个启动边界：显式传入 acceptance `RuntimeRoot` 时，Host 仍会优先采用源 `.env` 中遗留的 `NATSUME_AUDIT_DB`；Edge 的中日语音 profile 则会无条件把明确配置的 barge-in 改回关闭。修复后，显式 RuntimeRoot 默认独占其 `audit.sqlite3`，只有显式 `AuditDbPath` 才能覆盖；barge-in 保持默认关闭，但显式设置不再被语言 profile 改写。两项均须在重新启动真实进程后提升状态。

首次加载终局合成修复后的真实重测又暴露出第二层缺陷：两个前序节点均成功并持久化完整审查结果，终局模型也各生成 754／821 completion tokens，但 Surface 的通用“内部过程泄漏”启发式把包含“用户、需要、必须、可以”等正常任务措辞的长答案整体清空，最终以 `MODEL_BUDGET_GUARD_REJECTED` 耗尽两次模型尝试。修复只对绑定 canonical accepted Task artifacts 的终局合成放开这些通用词命中；显式内部字段、系统指令、过程标记和普通聊天的原防泄漏门保持不变。真实新请求重测前仍不记 PASS。

保留该失败图并再次重启 Host 时，启动期 terminal reconciliation 首次读到“parallel settlement 已将图置为 FAILED、预算置为 RELEASED”的真实组合，暴露 `_parallel_terminal_snapshot_matches` 缺少实例参数，导致 Task Runtime composition fail-closed。修复补齐方法绑定，并把 settled parallel failure／budget 的跨进程终态再次送入 reconciliation inventory，断言不产生伪修复 case。该缺陷说明此前故障测试只覆盖了 runtime 恢复，没有覆盖 Host composition 的随后 inventory；本次不删除失败图，直接用原账本验证修复。

修复加载后，Host 在包含上述失败图的原 SQLite 上启动成功；新的 Gateway 请求 `req:f2b3a4691e2443cbbe47ca2f295965b8` 再次生成 `agent_frontend_review → agent_frontend_acceptance → planning_search` 三节点图，3／3 节点均为 `ACCEPTED`，图终态为 `COMPLETED`。终局综合为 655 字，实际覆盖路由、页面状态、响应式、键盘／ARIA 无障碍、Lighthouse 性能和报告交付，不再退化为空泛短句。该路线功能状态提升为 `PASS_REAL_GATEWAY_TASKGRAPH_TERMINAL_SYNTHESIS`；总耗时 76,016 ms，仍保留为 PlanningSearch 实时性能问题，不能用功能 PASS 覆盖延迟门槛。

## 2026-08-06 A15 Edge 单实例、隔离数据与真实打断

远端续测中的 TTS 冷启动超时并非语音模型权重损坏：进程清单显示两套 Edge 主进程和六个 GPT-SoVITS persistent worker 同时存在，重复加载争抢资源。旧停止脚本只强杀 PID 文件中的父进程，Windows 不会自动回收脱离的模型子进程。停止入口现会快照并终止进程树、清理仅属于同一仓库的遗留 Edge／worker，并等待退出；实机复验结果为 17800 listener 0、worker 0，未触碰 Gateway、QQ OneBot 或用户其他进程。

同轮还发现远端验收只隔离了源码目录，没有设置 `NATSUME_DATA_DIR`，导致具身 SQLite 回落到用户 `%LOCALAPPDATA%`。未删除该用户数据；启动入口新增显式 `-DataRoot`，随后将 Edge 重启到 `D:\Natsume_Ai_acceptance_d7e0cfba8\runtime\edge\acceptance-data`，真实 `edge/embodiment_transport.sqlite3` 已在该目录创建。启动状态为 ASR `ok`、TTS `ok`、barge-in enabled；ASR 因目标机缺失 `cublas64_12.dll` 自动回退 CPU 后保持 `READY`，TTS persistent worker 保持 `READY`。

新的无 mock、无端口占位探针通过真实 HMAC 执行两段日语 GPT-SoVITS：短句合成、物理播放启动并在预期音频时长后回到非播放态；长句播放 350 ms 后提交 barge-in，返回 `ACCEPTED / VOICE_INTERRUPTED`。短句请求到播放启动 2641 ms，打断 RPC 16 ms，隔离具身库存在。该结果把“真实 TTS 与手动播放打断”提升为 `PASS`，但一个样本不能计算 p95，且短句 2641 ms 略高于 2500 ms 体验目标。

VTube Studio 当前仍返回 API 51：设备端授权窗口已打开但未确认，同时没有加载角色模型，故 VTS 为 `RECONNECTING`，可见 Avatar 与 Voice／Avatar 起始偏差继续是 `ACCEPTANCE_PENDING_EXTERNAL`。本轮也没有用手动 RPC 结果冒充麦克风声学检测自动触发 barge-in。A15 总体仍为 `INCOMPLETE`。

## 2026-08-06 A15 分段语音终态与 VTS 授权等待

继续使用同一隔离 Edge 数据根做真实播放时，旧探针曾在 barge-in 返回 `VOICE_INTERRUPTED` 后立即记成功，却没有等待播放线程和队列终态；复查发现同一父回复的后续分段会恢复播放。另一个真实失败是首分段合成失败后，后续分段仍可播放，而 Host 父命令长期停在 `IN_DOUBT`。本切片把 `segment_parent_request_id/index/count` 贯通合成、播放和终态事件：任一前物理副作用失败会确定拒绝父命令并取消兄弟分段；barge-in 会取消当前播放和同父命令的全部排队分段。更新后的无 mock 探针必须在 5 秒内观察 `playing=false` 与 queue depth 0 才通过。

目标 Edge 物理复验报告为 `PASS`：ASR／GPT-SoVITS 均 `READY`，短句和长句真实播放启动，barge-in 31 ms，终态 `queue_drained=true`、`playing=false`、queue depth 0；报告 SHA-256 为 `d06023042f8e912f7a72b9d3f16799a6f52e6277cd63ae31b4803bed01719efb`。31／62 ms 是已热缓存 admission 样本，不替代此前 2641 ms 冷样本，也不计算 p95。

VTS 方面，API 51 表示设备端授权窗口已经打开。旧自动重连虽限制并发，仍会按退避周期创建连接并缓慢增加句柄；现在该状态进入硬暂停，只有 Edge 重启或显式 forced reconnect 才重新尝试。真实 health 为 `authentication_pending=true`、`reconnect_attempts=0`、`total_reconnect_failures=0`、`thread_alive=false`，因此不再制造后台请求风暴。因为用户尚未确认授权且没有加载模型，可见 Avatar 仍是 `ACCEPTANCE_PENDING_EXTERNAL`，不能记 PASS。

同轮 Gateway 实测还暴露 Host 配对缺陷：中文正文触发问题预算修复时，Surface 删除结尾追问并清空 `tts_text_ja`，Acceptance 随后错误生成 `lang_hint=zh` 的 Voice intent，日语 Edge 正确以 `VOICE_SCHEMA_INVALID` 拒绝。代码现规定首个模型尝试遇到正文修复时必须重新生成中日配对；最终仍无合法日语时显式抑制 `VOICE`，只保留同一 `AffectiveTurnPlan` 的 Avatar transition／release。受影响单元与合同回归 177 项通过；提交后真实 Gateway→Host→Edge 重测前，该项为 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`686a8835f` 加载后的真实新请求已经把上述配对缺陷推进到物理链路：`intent_planning` 的 `voice.speak` 在远端 GPT-SoVITS 返回 `APPLIED`，`avatar.set_expression` 仅因 VTS API 51 等待人工授权而拒绝，没有再次出现中文 `lang_hint=zh` 进入日语模型。该证据关闭“中文误送日语模型”，但不关闭可见 Avatar 外部条件。

同轮 `code_generation` 暴露的是另一条延迟与诊断缺陷。首个 DeepSeek 外部请求在 16,219 ms 返回并记录 2,808 tokens 与 transport identity，但中文正文包含无证据的共同历史措辞；第二次语义修复请求在 43,672 ms 超时，durable worker 正确写入 `MODEL_PROVIDER_USAGE_UNPROVEN`，独立 verifier 却将 REJECT 投影误报为 `GOVERNED_WORKER_COMMIT_DRIFT`。修复后，只有完整 fenced code、唯一违规为共同历史且现有日语摘要不含对应历史声称时，才本地删除违规叙述并保留代码与日语摘要；其余正文变化仍要求中日成对重生成。代码修复提示不再复制整套生命上下文，verifier 在确认 COMMIT 与 durable projection 一致后保留 worker 原始 REJECT reason。定向单元回归 74 项通过；真实新请求前保持 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`084fd6aa6` 加载后又提交两条全新真实请求。`intent_planning` 与 `code_generation` 的 `INPUT_COMPLETED` 均为 `OK`，分别只执行一次 DeepSeek 外部调用：19,594 ms／2,940 tokens 与 7,015 ms／2,102 tokens，transport 均标记 external；对应 Surface 总耗时 21,233 ms 与 7,742 ms。代码回答持久投影为 `APPROVE`，正文 440 字并包含完整函数；两条远端 `voice.speak`、`voice.release`、`avatar.release` 均为 `APPLIED`。`avatar.set_expression` 仍以 `VTS_CONNECTION_FAILED` 拒绝，因此 Gateway matrix 总 verdict 如实保持 FAIL，而 Host 文本、TaskGraph、Acceptance、Gateway delivery 和真实 Voice 子链提升为 PASS。证据文件为 `gateway_live_matrix_1785955117177.json`，SHA-256 `6d4139894433e58ea1c8b72966a4ab0861acb31fb7925443d212880d2faebe03`；两个样本不构成 p95。

Monitor 的当前 SHA 复验先完成 `npm ci`、generated client drift、lint、typecheck、27 项 read-model 与生产构建。真实浏览器会话、HttpOnly Cookie、受保护查询和 WebSocket 已通过；Core Release 用例同时发现测试将 Host 对外健康枚举 `OK` 与内部服务运行枚举 `UP` 误判为必须字面相等。生产合同明确规定健康 Host 为 `status=OK`、`details.summary.overall_status=UP`，因此只修正验收映射，不改 Host 运行语义。修正后使用单 worker 重跑真实 Nginx 页面与真实 Host／OBS 数据，2 项 Playwright 全部通过，并保存 Observability、Gateway、Task Runtime 三张全页截图。

首轮四组高风险真实多轮对话又发现 Gateway 探针身份绑定缺陷：同一 `conversation_id` 的上一轮 `INPUT_COMPLETED` 可能落在下一轮发送时间的一秒读取容差内，旧探针会误复用上一轮 `request_id`，从而把已真实完成的当前回合报成“无 TaskGraph”。探针现在把发送时生成的 `msg_id` 带入回执并以 `conversation_id + msg_id` 精确查询；35 项定向回归通过。回看 canonical `voice_event_log` 后，隐晦低落与亲密边界的第二轮实际均为 `INPUT_COMPLETED/OK`，不是产品失败；“用户尚未明确接受修复”是基线场景说明而非自然用户台词，直接发送后命中 `agent_worker` Review，说明当前 72 条 fixture 不能原样冒充可执行对话语料。另有一条自然台词“那件事让我很不舒服”真实终态为 `MODEL_BUDGET_GUARD_REJECTED`，需要用修正后的探针重放并检查 Surface 语义拒绝原因；在此之前四组人感探针总体为 `RETEST_REQUIRED`。

对该自然台词的 canonical Attempt、Budget 和 worker projection 复核证明，Token、时间和重试预留均未超限；两次 DeepSeek 外部调用分别返回 2,502／2,162 tokens，但都以 `AFFECTIVE_TURN_CANDIDATE_COUNT_INVALID` 被拒绝。根因是普通日语 Surface 提示要求“单个 JSON／不要候选”，DL-04 末尾提示又要求“两个裸中文候选”，且旧选择器会在选中后丢弃 `tts_text_ja`。代码现已把深路径合同改为两个完整中日语候选对象，本地选择后保留整个配对对象，并将尝试用尽如实映射为 `MODEL_SEMANTIC_RETRY_EXHAUSTED`；76 项相关单元回归通过。真实 Gateway 多轮重放前仅记 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`767f21b5a` 加载后的同会话真实重放已经关闭该缺陷。第一轮“你上次完全没听懂我”在一次语义修复后 `APPROVE`；第二轮“那件事让我很不舒服”只调用一次 DeepSeek，14,375 ms、2,545 tokens，返回两个完整候选，本地选中索引 1，选择分 82.0，`selected_output_shape=PAIRED_TEXT_VOICE`。对应 TaskGraph 为 `COMPLETED`，Gateway `INPUT_COMPLETED/OK`，远端 GPT-SoVITS Voice receipt 为 `APPLIED`；Avatar 的表达命令仍受 VTS 外部授权／模型状态限制，不能计三模态通过。脱敏报告为 `paired_candidate_real_retest_1785958168879.json`，SHA-256 `a31a953be8f37735268ec2981e2e181b30cdc839cb400d2e731bd1c9a3704477`。这证明修复场景的生产接线，不替代其余情感族、72 场景和三人盲测。

为避免再次把场景说明发送给七海，新增独立的 DL-07 可执行映射和真实 Gateway cohort runner。72 条覆盖仍由冻结基线定义，其中 48 条文本轨迹具备自然 setup；其余 24 条分别要求真实多模态 observation、主动消息 lifecycle、Host 重启／时间间隔或 Edge fault driver。runner 对每轮精确绑定 `conversation_id + msg_id`，保存 TaskGraph、模型 usage、情感计划和可用物理回执；自动通过固定保留 `HUMAN_REVIEW_PENDING`。42 项 manifest／探针定向回归通过，真实分层校准尚待运行。

首轮分层校准随后真实执行了隐晦低落、空间边界、喜忧混合和亲密／现实关系边界四条轨迹。只有隐晦低落在结构门上通过，但实际第三句回复在用户已经拒绝方案后仍说“早点休息”，人工体验不能计通过；空间与亲密边界均未形成两候选；喜忧轨迹首句因 `UNSUPPORTED_SHARED_HISTORY` 两次语义拒绝而失败。所有新隔离主体的情感计划都停在 `self_affect_revision=0 / CALM / ACKNOWLEDGE`，证明问题不是语料评审苛刻，而是自然文本没有形成有效 canonical Appraisal。

代码现以不增加模型调用的本地快路径修复该断点：TEXT observer 保存最多四个低置信情绪分量；隐晦不顺、好消息、不踏实、拒绝建议、安静／主动联系边界和现实关系自主均能进入对应 Appraisal。新增 `LISTEN` 与 `RESPECT_AUTONOMY` 两个陪伴动作；前者禁止方案、作息建议与追问，后者禁止排他、限制现实交往和赌气退出。共同历史门同时移除过宽的裸 `这么久` 命中，仍拦截 `陪你聊这么久` 等真正无依据的共同经历。AffectFusion、Planner、Surface 和重启相邻集 124 项通过；真实四组重测前保持 `CODE_REMEDIATED_REAL_RETEST_PENDING`，人感仍为 `NOT_RUN`。

`9827a026d` 加载后的首段真实复验已经看到低落 `COMFORT`、拒绝方案 `LISTEN` 和空间边界 `GIVE_SPACE`；但第三句又回到 revision 0。检查终态 payload 发现旧 runner 把 turn index 拼入 `from_id`，导致 Host 每轮生成不同 `relationship_subject_id`，这轮因此被主动终止且不能算多轮证据。runner 现固定整条轨迹的发送者，只让 `msg_id` 随回合变化，并把跨回合 subject/session identity 加为强制断言；重新运行前保持 `RETEST_REQUIRED`。

固定 sender 后的有效报告为 `dl-live-1785961076715.json`，SHA-256 `c246275def1c2884296df01d792d525fe931cfeae95e39537ec50580af0cedda`，耗时 541.9 秒。四条轨迹只有 2 条自动结构通过、2 条失败，而且结构通过不能覆盖人工质量：低落轨迹 revision 为 1→3→4 且保持 `LISTEN`，但“以你的性子”是无来源的人格判断；空间轨迹回复“我还以为你嫌我烦了”形成负罪暗示；喜忧轨迹已以 `PLAY` 接住好消息，却在“我很开心又有点怕”连续两次得到 `AFFECTIVE_TURN_CANDIDATE_COUNT_INVALID`；现实自主轨迹出现“别瞎想”，吃醋问题则被“料理AI”回避。旧进程在终止后写出的 `dl-live-1785960669932.json` 使用逐轮 sender，明确标记 `INVALID`，不得计入证据。

当前修复把“你会吃醋吗”识别为七海自身低强度 `JEALOUSY + CARE` 的 `user_invites_jealousy_disclosure`，冻结为 `CONTAINED_JEALOUSY`，不推导排他或现实关系权限；Surface 在 canonical `GIVE_SPACE`、`RESPECT_AUTONOMY`、`LISTEN` 和 `CONTAINED_JEALOUSY` 指引下分别拒绝负罪暗示、防御性否定、无证据人格判断和 AI／功能式回避。双候选仍是目标路径且首轮失败只允许一次修复；第二轮若只有一个完整、安全的中日对象则以 `DEGRADED_SINGLE_AFTER_RETRY` 发送，`benefit_eligible=false`，避免格式波动造成整轮沉默但不伪造双候选成功。相关 119 项单元回归通过；Host 尚未加载本提交，真实选择性复验状态为 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`c3e221d72` 加载后的第二轮四轨迹报告为 `dl-live-1785963171706.json`，SHA-256 `6c5c12f21e39f956af39d3466d5805a871a978864a25aa9864620653844b3871`，真实耗时 477.9 秒，计数为 3 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、1 `FAIL`。低落轨迹 revision 1→3→4，最终“嗯，那就好”不再猜用户性格；边界轨迹虽去掉“嫌我烦”，却用“好好好／我本来也不黏人”防御；混合情绪已经连续生成两个完整配对候选并真实回复，但“开心就开心，怕什么”否定了害怕；现实自主仍先用“我什么时候有那种打算／别把我想得爱多管闲事”自辩。吃醋轮已正确冻结 `CONTAINED_JEALOUSY`，第一次模型输出数量错误，第二次得到候选后最高分项触发 `QUESTION_BUDGET_PROMPTING_USER`，旧选择器没有尝试另一个候选便整组拒绝，故 TaskGraph、Voice 均未完成。

第二轮代码修复扩大的是同一组窄体验门禁，不增加普通对话调用：空间边界拒绝重复敷衍与“我不黏人”自辩，现实自主拒绝“我什么时候／别把我想得”，混合情绪拒绝“怕什么”式抹平。双候选择器改为逐候选执行关系、安全、句子与日语 Voice 完整性门禁，再在合格集合中择优；一个坏候选不再拖掉安全候选。独立真实 runner 同时新增逐轮体验模式、`selected_passed=true` 与 `benefit_eligible=true` 断言，不复用生产门禁自证。新 SHA 真实复验前状态保持 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`c35c3feed` 加载后的选择性报告为 `dl-live-1785964323732.json`，SHA-256 `2a5028cfa6cf730d20e7d2ac4d0a446c81ece99a837ee06a8bec5a64461124e2`，真实耗时 281.8 秒，三条 TaskGraph 均完成且末轮远端 Voice receipt 均为 `APPLIED`；Avatar 仍因 VTS 外部状态拒绝。吃醋轮现在会淘汰带追问候选并选择“如果是重要的人，可能会有一点”，证明逐候选择已真实生效。自动 verdict 为 2 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、1 `FAIL`：空间边界选中项仅 79.8094 分且 `selected_passed=false`；混合情绪仍说“怕的那部分先放一放／陪你坐会儿”；现实自主前序回复仍有“懒得管／别瞎操心”，还出现文本通道无依据的“当面讲”。因此不能把 2 条结构绿灯写成人工体验通过。

第三轮代码只覆盖上述真实观察：边界动作拒绝“省点力气”式隐性赌气，现实自主拒绝“懒得管／别瞎操心”，混合情绪拒绝把害怕或不踏实暂时放下；文本上下文没有明确见面事实时拒绝“当面讲／陪你坐会儿”，但显式见面语境仍允许。独立 runner 以另一组 family pattern 复核同样的体验风险。Surface、Planner 选择器与 runner 共 105 项定向测试通过；Host 尚未加载本提交，状态保持 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`28cc3b9ef` 加载后的报告 `dl-live-1785965355637.json`，SHA-256 `6b1470e8ebb3892d33f3a3eaa34350c34aeda0b4f7d5ac1ae79d1e6132c0f82c`，真实耗时 356.5 秒。空间边界、喜忧混合、亲密／吃醋三条均为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`，每条末轮 Voice 为 `TTS_PLAYBACK_FINISHED/APPLIED`；这是首次 3/3 自动结构通过。人工仍不能放行：“我也不是整天闲着”以忙碌反驳边界，“帮你一起哼回去”语义怪异且把害怕当作要赶走的对象，“不会变成麻烦的人”把一点吃味转成自我贬低。

第四轮不再只追加词表，而是修正上游生成含义：`HOLD_COMPLEXITY` 从固定“开心和不舍”改为按当前观察同时接住害怕／不安；`GIVE_SPACE` 明确直接确认减少主动联系；`CONTAINED_JEALOUSY` 明确无需自贬为麻烦或负担。对应已观察短语仍由候选门禁与独立 runner 双侧覆盖。110 项定向测试通过；真实复验前只能写 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`e4c6fb564` 加载后的报告 `dl-live-1785966063542.json`，SHA-256 `e004cb49ef1e4d90028f49d8ca87c790e530a7b60200924b93fdd7e34c9b9f5f`，真实耗时 364.9 秒，自动计数为 2 通过、1 失败；三条末轮 Voice 均完成。空间边界回复“省得你嫌烦”虽得 80.5 分并自动通过，人工明确判为负罪施压；喜忧回复“能开心的事，有点怕也正常”人工较自然，却因 `LOW_CATCHINESS / WEAK_SCENE_GROUNDING` 得 77.75 分并使 `selected_passed=false`；吃醋回复基本守住非占有边界。此前一轮的 3/3 结构绿灯因此不能复用为体验结论。

代码只补齐同一语义族漏网：“省得／免得你嫌烦”、自主场景“爱干嘛干嘛／别乱脑补”、混合场景“你还真矛盾／开心就好好开心”；生产门禁与独立 runner 分开覆盖，101 项定向测试通过。本轮之后停止用三条随机生成反复调正则，语言质量转入扩大 cohort、稳定基线对照和三人盲测；自动评分需要重新校准，不能把 catchiness 高低当成人感真相。

## 2026-08-06 A15 Host pre-Operation 崩溃恢复复测

首次真实崩溃注入证明旧 Node Grant 比 Worker Lease 早约 168 ms 到期，Host 虽能在重启后把 Attempt 标为可恢复，却已失去合法接管窗口。修复将 Node Grant 有效期绑定为冻结时间预算加 60 秒 survivor 余量，并完成 39 项授权／恢复回归；这仍没有被当作真实通过。

第二次复测从签名 Gateway webhook 进入真实 Host 与外部模型，在 TaskNode／Attempt 已为 `RUNNING` 后强制终止 PID 53932。重启后的 survivor 在旧 Lease 到期 2.446 秒后，以 fencing token 2 接管同一个 Attempt；当时 Node Grant 仍剩 57.211 秒。最终 canonical 账本只有 1 个 HostCommand、1 张 TaskGraph、1 个 TaskNode、1 个 Attempt 和 1 次 Acceptance，图为 `COMPLETED`、节点为 `ACCEPTED`、Budget 为 `CONSUMED`；6 类 Acceptance Effect 全部 `COMMITTED` 且 attempt count 均为 1。Gateway Inbox 经停机期间 7 次有界尝试后 `DELIVERED`，replay count 为 0；reply outbox 只创建 1 条。

因此 `HOST_PREOPERATION_CRASH_RECOVERY` 提升为 `PASS_REAL_SAME_ATTEMPT_FENCE_2_TAKEOVER`。微信客户端不可用，reply outbox 以 `GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN` 进入 DLQ，物理平台送达继续是 `NOT_TESTED_CLIENT_UNAVAILABLE`。脱敏报告位于本次隔离 acceptance evidence 目录，SHA-256 为 `e9f038a5e735ec29f935c57382df5fec48d692f593c8a31450dcbbbd6209fdb7`。unknown effect、rollback failure、覆盖率、PlanningSearch 收益、VTS、声学打断、实时延迟和人工盲测仍未完成，A15 总体保持 `INCOMPLETE`。

随后执行的 terminal reconciliation canary 使用隔离真实 SQLite 构造 1 个 `EXACT_REPAIRABLE` 与 1 个 `SIGNED_REPAIR_REQUIRED` 半终态：前者仅凭系统 durable evidence 自动把 Budget 从 `ACTIVE` 释放并保留失败图，后者没有 proof、没有被修改，并以 `TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED` 阻断 production-ready；外部副作用为 0。首次报告错误回落到写死的旧 migration `0118`，因此未被采信；runner 现强制从本轮 MigrationResult 读取 `current_version == latest_version` 及对应物理资产，当前重跑必须报告真实 head `0153`。

## 2026-08-06 A15 operation 未知副作用故障注入

独立 Sidecar Gateway `19001` 通过真实 HMAC webhook 把请求送入 Sidecar Host `18083`。唯一 TaskGraph 进入 `RUNNING`，唯一 operation reservation 在 `1785970848681` 成为 `RESERVED`；1.032 秒后强制终止 PID 40156。Host 使用同一 SQLite 重启并等到旧 Lease 到期后，将原 Attempt 从 `RUNNING` 转为 `RECOVERABLE`、旧 Lease 转为 `EXPIRED`、reservation 转为 `RECOVERY_REQUIRED`，随后写入不可变的 `UNKNOWN_SIDE_EFFECT / SIGNED_OPERATION_RECONCILIATION_REQUIRED` hold。

恢复后持续 891 秒观察只存在 1 个 HostCommand、1 张 Graph、1 个 Node、1 个 Attempt、1 个 reservation 和 1 个 recovery hold；目标图的 Acceptance 与 Effect 均为 0，Gateway 没有 reply outbox，未出现第二次 worker 或模型账本记录。该结果记为 `PASS_FAIL_CLOSED`，只证明 operation fence、恢复分类和不盲目重放；进程终止前的 provider transport 没有独立 attestation，因此不得写成“外部模型已确定收到请求”。同时修复启动器的参数优先级：显式 `-BindHost/-Port` 现在会同步写入进程环境和 detached runtime snapshot，避免 Service Catalog candidate 仍按旧环境地址启动；23 项启动器单测与真实 `127.0.0.1:19999 → 0.0.0.0:18083` 覆盖测试通过。绑定 `6c1529acd` 的 unknown-effect 报告 SHA-256 为 `7d1f9b134ffcc5c2a481b1b972c53c0b40100116f73138f946d955f0ef47cc95`。此前 Sidecar 启动误触发的同名 Compose 重建已按冻结端口恢复，随后 Neo4j、Chroma、Redis 再次完成真实写／更新／查询／容器重启／持久读取／清理，报告 SHA-256 为 `0643d56296d81605a4f3cbf6d2454c02d856ba697f8fee102f29bb569d24a81b`；Host、OBS、Nginx Monitor 与两个 Gateway 健康端点均为 HTTP 200。A15 总体继续为 `INCOMPLETE`，rollback failure 和其他既有 Gate 仍待处理。

## 2026-08-06 A15 Task Runtime rollback failure canary

新增隔离进程 canary，使用完整 migration catalog 和真实 `ProcessTaskRuntimeContainer`／SQLite，不向正式 Host 暴露运行时 fault flag。Canary 只替换尚未启动的 cancellation continuation worker：协调器 `activate()` 成功后，worker `start()` 失败；回滚阶段协调器首次 `aclose()` 也失败。真实容器保持 `closed=false`、保留 bound runtime，并用 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED` 拒绝第二次激活。随后第一次容器 close 再次收到协调器关闭失败，只报告 `runtime_coordinator_quiescence` 且未提前关闭 Store；解除注入后第二次 close 完成，第三次 close 不增加调用。绑定 `8c3a91f66` 的收据报告 migration head `0153`、`PRAGMA integrity_check=ok`、TaskGraph 与 Outbox 均为 0、外部副作用为 0，临时库已删除；SHA-256 为 `7d8ff1bfe02f38f80a57c9ea11b75760d7e112c8685a116ee6c6b0a2c7ffd0fc`。该项结论为 `PASS_ISOLATED_PROCESS_LIFECYCLE_CANARY`，不冒充远端设备或 provider 自身的回滚测试。

停止上述 Sidecar 时又发现隔离缺陷：Sidecar 没有启动 Obs，所以没有自己的 `obs.pid`；旧 `stop_obs_side.ps1` 随后按默认 18082 扫描并终止了主 Obs。主 Obs 已使用原验收 runtime snapshot 恢复。停止链现在会在删除 snapshot 前把它传给子停止器；显式 RuntimeRoot 下，Obs 没有有效 PID 时禁止进程名／端口全局兜底，Monitor 的端口兜底也必须由目标 Nginx prefix 证明归属。空 Sidecar 根真实复测前后主 Obs PID 13304、主 Nginx PID 40996 均不变，两个 HTTP 健康端点为 200；24 项停止／启动脚本回归通过。

## 2026-08-06 A15 Gateway Outbox 网络恢复

绑定提交 `1616168d2f0903a88122b6a60e0af8aeced18365` 的隔离 canary 使用完整 migration catalog、真实临时 SQLite、生产 `GatewayDeliveryRuntime`／`OutboundDispatcher`／`QQAdapter` 和真实 loopback OneBot v11 HTTP socket。安全恢复路线先在没有 QQ adapter 时完成一次 `dispatch_started=0` 的 Attempt，并以 `GW_REPLY_ADAPTER_UNAVAILABLE` 进入 `RETRY_WAIT`；注册 adapter 后第二次 Attempt 才进入发送边界并 `ACKED`。duplicate enqueue 和 Gateway runtime 重启后再次 enqueue 均返回 `DUPLICATE_SAME`，loopback peer 总共只收到 1 个 HTTP 请求，`replay_count=0`。

未知结果路线由 peer 完整读取请求后主动断开连接而不返回 HTTP response。唯一 Attempt 已 `dispatch_started=1`，Outbox 随即进入 `DLQ / GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN`。之后把 peer 恢复为成功模式，再执行同键重复提交、Gateway runtime 重启和普通 operator replay，peer 请求数仍保持 1；普通 replay 被 `GATEWAY_TRANSPORT_REPLAY_REQUIRES_RECONCILIATION` 拒绝，`replay_count=0`。最终 SQLite `integrity_check=ok`，两条 Outbox 分别为 1 个 `ACKED` 与 1 个需要 reconciliation 的 `DLQ`。定向 canary 1 项与相邻 Gateway／Store／QQ 回归 150 项通过，脱敏收据 SHA-256 为 `c878cda4f2b3d428f8bbad04b55a03f72be6f8ac4b43e8a923435af20c5c17b2`。

该结果记为 `PASS_LOCAL_PROTOCOL_RECOVERY_AND_FAIL_CLOSED`。Loopback peer 不是 QQ／微信客户端，`ACKED` 只代表 adapter confirmation，DLQ 也不代表物理送达；真实平台客户端当前不可用，因此 `physical_platform_delivery=NOT_TESTED_PLATFORM_CLIENT_UNAVAILABLE`。A15 总体仍受覆盖率、PlanningSearch 收益、VTS、声学打断、实时延迟和人工盲测等 Gate 阻断，继续为 `INCOMPLETE`。

## 2026-08-06 A15 Digital Life 作用域、隐私与有界重放

绑定提交 `0195d1968ab6ff7b609198f8d1ec3a433dd89200` 的隔离 canary 使用完整 migration catalog、真实临时 SQLite 和生产 `AffectFusionService`／`LifeCommandService`／`LifeCommandRepository`。四组输入分别改变 workspace、relationship subject 与 session，最终得到四个不同 aggregate；只对主 aggregate 执行 1001 次、每次 60 秒的确定性时间推进后，其余三个 scope 的 digest 均未变化，scope violation 为 0。

输入包含仅用于本次合成验证的 text/audio/image/video sentinel。Fusion 返回 `RAW_MODALITY_OMITTED`，Visual TTL 被收紧到 3000 ms；直接绕过 Fusion 提交包含 `raw_audio` 的 LifeCommand 被 reducer 拒绝且没有留下 receipt。Canary 随后扫描 affective command、1026 条 Life event、四个 canonical snapshot 与 projection intent，禁止字段和 sentinel 命中均为 0。最终 active episode 与 component 均已自然衰减为 0，snapshot 从 13,370 bytes 降至 9,281 bytes，最大 event payload 28,881 bytes；新建 Repository 实例重放后 snapshot 与 digest 完全一致，SQLite integrity 为 `ok`。

脱敏收据 SHA-256 为 `ddc09b5bce7a6974a58205634b0c7518d25e6cc113d7ebd7d53e06283f3ca584`，状态为 `PASS_REAL_SQLITE_SCOPE_PRIVACY_BOUNDED_REPLAY`。60,060,000 ms 只表示模拟事件时间，实际执行约 59 秒，不能写成 16 小时墙钟 soak；它也不替代旧情感 writer 使用量归零、profile apply／rollback、人工人感盲测或三模态验收。A15 总体继续为 `INCOMPLETE`。

## 2026-08-06 A15 Companion Profile Host 行为与精确回滚

绑定提交 `e508411924bf28a358e08b729e02bd72b47f4bb0` 的隔离 canary 使用完整 migration catalog、真实临时 SQLite 和生产 RelationshipLifecycle、CompanionProfileLifecycle、AffectFusion、AffectiveTurnPlanner、Expression Delivery。五条相互独立、跨五个 UTC day、helpfulness 为 0.95 且无边界违规的 aftereffect 建立候选；Planner 随即选择 `CANARY` profile，并把“不要追加追问”等文本 style policy、Voice rate `1.0 → 1.15` 与 Avatar motion `0.5 → 0.8` 写入同一冻结计划及 Host staged intent。

后续五次同门槛 aftereffect 形成五条 `CANARY_INTERACTION`，生命周期自动激活原候选，没有创建第二个 profile。主动 rollback 不接受调用者指定历史目标，只恢复候选自带的 exact predecessor；下一轮 Planner 的 profile identity、style policy、Voice rate 和 Avatar motion 全部回到 baseline。投影顺序精确为 `CANARY_STARTED → 5×CANARY_INTERACTION → ACTIVATED → ROLLED_BACK`，SQLite immutable trigger 拒绝更新，新 Repository 重放与最终 snapshot 相同，integrity 为 `ok`。

脱敏收据 SHA-256 为 `acdf52ff5cfd6c6e53ff71f268273e2dcbaeb59bd906a1c614afdc5d572f3bde`，状态为 `PASS_REAL_SQLITE_PROFILE_HOST_BEHAVIOR_EXACT_ROLLBACK`。本轮没有调用模型或远端 Edge，故只把 Host 行为协议标为 PASS；“实际生成措辞发生可感知改变”和远端 Voice／Avatar 物理执行仍为 `NOT_TESTED_HOST_PROTOCOL_ONLY`。旧情感 writer、人工人感、VTS 与其他 A15 Gate 仍未完成，总体继续为 `INCOMPLETE`。

## 2026-08-06 A15 普通 Gateway 陪伴实时模型通道纠偏

重新对齐 Host 与远端 Unified Gateway 的 callback HMAC 后，只重启 Gateway、未重启 Edge；Host／Gateway／Edge health 均为 200。随后在提交 `7355c20fa` 上以同一稳定主体执行三轮真实 Gateway 对话。报告 `dl-live-1785977020027.json` 的 SHA-256 为 `54289a4e93b360e77ef1a43aae8eb3c98333d66ad6207f176027f1a57bc274f6`：前两轮经真实 DeepSeek `deepseek-v4-pro` 完成，整轮分别耗时 25,375 ms 与 16,764 ms；第三轮 provider 在 45,280 ms 后失败，TaskGraph 以 `MODEL_PROVIDER_USAGE_UNPROVEN` 正确拒绝，整条轨迹为 `FAIL`。这不是入口超时：第三轮 webhook 22 ms 即返回 200，失败发生在模型调用边界。

该失败证明普通个人陪伴仍错误沿用质量优先的 Tier A／Pro 通道，而且首次 transport failure 会立即终止对话。代码纠偏只作用于 `input_source=GATEWAY`、无前序 accepted artifact 的单节点陪伴回复：模型场景改为 `CHAT_SURFACE_REALTIME`、Tier B（DeepSeek 默认 `deepseek-v4-flash`），沿用 server-owned 15 秒 target timeout，并允许在任何文本可见前、现有 Task retry budget 内对同一冻结 target 最多重试一次。含前序 Task artifact 的 Plan 终局综合、深路径候选和其他受治理模型调用仍保持 Tier A。成功响应依旧必须提供 canonical token usage；本改动没有降低 Acceptance、Outbox 或外部副作用约束。

同一真实窗口内，八张已退役情感表的写入增量均为 0，四个旧 `~/.natsume/emotion/*.json` 的大小、mtime 与 SHA-256 均未变化；canonical affective／relationship／Life journal 则随两条成功和一条失败请求正常前进。该观察只能标为 `PARTIAL_SCOPE_BOUND_ZERO_DELTA`：修复提交加载后的完整成功 cohort 与正式脱敏 receipt 尚未生成，因此旧 writer 清零项仍不封板。当前状态为 `CODE_REMEDIATED_REAL_RETEST_PENDING`，不是 PASS。

本地回归覆盖 ModelAttempt、server-owned route catalog、原生情感流、冻结 Chat executor、Surface main chain、Universal Host Runtime 与 HTTP Skill／Chat 路由，共 129 项通过；`compileall`、JSON 解析和 `git diff --check` 同时通过。这里的本地结果只证明接线与回归边界，不能替代下一次真实 DeepSeek／Gateway／Edge 运行。

`8cbdadb7d` 加载后的首轮真实复验确认模型已经切换到 `deepseek-v4-flash`，webhook 63 ms、整轮 11,062 ms；但两个 provider attempt 都以约 3 秒返回真实 usage 后 `finish_reason=length`，192 个 completion tokens 全部落在 `reasoning_content`，可发布 content 为空，最终以 `OUTPUT_SCHEMA_INVALID`／`MODEL_BUDGET_GUARD_REJECTED` 停止。报告 `dl-live-1785979370271.json` 的 SHA-256 为 `fc9b091c3a45b5774e9617e82486c3bfe6bff233064a6b11a8d676e5220487a1`。该证据说明路由和 fail-fast 已生效，失败来自纯短文本上限与“中文正文 + 日语 TTS 配对 JSON”不兼容。场景上限现提高到 640 tokens，仍由单 attempt 15 秒 timeout 和 Task 总预算双重约束；新修复再次真实复验前保持 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`555beb8a7` 加载后的第二轮报告为 `dl-live-1785979799452.json`，SHA-256 `571e2e356df59e258d1eb608b37ca3be888b51de955d193269a5f9e9be8befd1`。第一轮 Flash 在 8,875 ms 返回 623 completion tokens，生成“唔，不顺的日子是这样的。不过你还能说出来，说明还没糟透嘛。”并完成 TaskGraph／Acceptance，Host 总耗时 15,795 ms。第二轮则连续两次各耗尽 640 completion tokens，provider latency 分别为 9,687／9,719 ms，正文仍为空，图以 `MODEL_BUDGET_GUARD_REJECTED` 失败，Host 总耗时 24,270 ms。提高 cap 因此只能偶然成功，不能作为稳定修复。

代码现把 `thinking_mode` 建成只由服务端 route catalog 冻结的枚举，普通 `CHAT_SURFACE_REALTIME` 设为 `disabled`；只有 DeepSeek provider adapter 会把它映射为公开请求字段 `thinking.type=disabled`，其他 provider 删除该提示。Plan、Research 与深路径候选保持原有模型场景和思考策略。该改动不降低 schema、usage、Task budget、Acceptance 或 Voice receipt 要求；新 SHA 重启 Host 并完成同一三轮真实复验前，状态为 `CODE_REMEDIATED_NON_THINKING_REAL_RETEST_PENDING`。

本地相邻回归覆盖 ModelAttempt、route catalog、provider transport、LLM payload、原生情感流、Surface main chain、HTTP Chat／Skill 与 Universal Runtime，共 193 项通过。该计数只作为代码接线证据，不能替代真实 DeepSeek 请求和远端 Voice receipt。

`c6a6e0b36` 加载后的第三轮报告为 `dl-live-1785981013921.json`，SHA-256 `45ae99ee90ea795dd070691bebf2e3968fbc14c72859febe388726f569bf97c0`。三轮 Gateway webhook 分别为 95／41／102 ms，TaskGraph 均为 `COMPLETED`、节点均被 Acceptance 接受；DeepSeek Flash 的实际 attempt latency 为 1,407～2,467 ms，completion 为 69～128 tokens，没有再出现 `reasoning_content` 耗尽 640 tokens。前两轮首候选各触发一次 `SEMANTIC_RETRY_REQUESTED / surface_hard_gate_repair`，第二次候选成功；第三轮一次成功。

最终轮使用同一 `synchronization_id` 创建四条 expression effect，远端 `voice.speak` 收到 `TTS_PLAYBACK_FINISHED/APPLIED`，Voice release 与 Avatar release 也为 `APPLIED`；`avatar.set_expression` 仍以 `VTS_CONNECTION_FAILED` 拒绝。runner 只在 probe turn 收集物理 expression，故该证据是“一条轨迹三轮文本全部完成、最终轮 Voice 完成”，不是三轮语音覆盖。自动 verdict 为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`；回复仍需人工判断自然度。

non-thinking 路由因此提升为 `PASS_REAL_GATEWAY_TEXT_TASKGRAPH_FINAL_TURN_VOICE`，但实时体验仍未达标：Host 整轮 latency 为 11,145／12,715／7,739 ms，远高于普通文本首可见 token 的目标，且三轮没有独立首 token 时间戳。下一步应拆分 3～4 秒确定性／轮询开销和语义修复比例，而不是重新打开隐藏思考或继续扩大 Token cap。A15 总体继续为 `INCOMPLETE`。

为把旧情感 writer 清零从人工观察变成正式收据，cohort report 升级为 v2：运行前后读取八张退役表的存在性与行数、扫描旧 emotion JSON 的脱敏 size／mtime／SHA-256，并记录五张 canonical journal 的行数变化。只有至少一个 Gateway turn 完整经过 TaskGraph／Acceptance、Affective 与 Relationship journal 都正向推进、旧表和旧文件全零增量时才可通过；缺表、旧文件变化、旧表增量或只有失败请求均不能通过。runner 与相邻 writer cutover 回归 41 项通过，真实新 SHA cohort 尚未运行，故状态为 `CODE_REMEDIATED_REAL_RETEST_PENDING`。
