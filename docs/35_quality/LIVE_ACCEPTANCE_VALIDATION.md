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
