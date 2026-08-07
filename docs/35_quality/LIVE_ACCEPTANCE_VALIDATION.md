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

绑定 `4f4008060` 的真实 v2 报告为 `dl-live-1785981812481.json`，SHA-256 `b511144d4fad3a9574e703cd4e33a658d86a7bd4ec0b89dc0acb5daba4b36450`。三轮均为 `INPUT_COMPLETED/OK`、TaskGraph `COMPLETED` 和 Acceptance `APPROVE`；最后一轮远端 Voice 再次得到 `APPLIED`。八张退役表的 before／after 均为 0，四个旧 emotion JSON 的脱敏文件集合与 size／mtime／SHA-256 全部相同。与此同时，`affective_life_event_v1 +8`、`relationship_life_event_v1 +6`、`life_event_journal +22`、`affective_turn_delivery_event_v1 +3`、`affective_expression_delivery_event_v1 +12`。该项提升为 `PASS_SUCCESSFUL_COHORT_ZERO_LEGACY_DELTA_CANONICAL_PROGRESS`。

本收据只关闭该 acceptance 数据根、当前运行窗口中的旧 writer 使用量；它不证明语言已经自然，也不证明远端 VTS。当前三轮 Host latency 仍为 13,112／8,655／8,795 ms，第一轮发生一次语义修复；A15 总体保持 `INCOMPLETE`。

继续对这三轮的远端 Gateway SQLite 做方向时间线核对后发现：第一轮 Reply Outbox 在 16.06 秒的平台调用后以 `GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN` 进入 DLQ；第二轮 Inbox 已在此期间提交，却直到 Outbox 返回后才获得 claim。根因是 `GatewayDeliveryRuntime` 原先由一个 worker 先跑 Inbox batch、再同步跑 Outbox batch。当前代码已改为独立 `gateway-durable-inbox` 与 `gateway-durable-outbox` 线程，两个方向继续使用原稳定 worker ID 后缀、Lease、fencing token 和原 DLQ 语义；新增合同测试让 Outbox 故意阻塞并确认 Inbox 在释放前已完成。

`4c66d61b8` 部署后的远端复验报告为 `dl-live-1785982868337.json`，SHA-256 `8709a206ce052a2eeb597f6ba549d3e5f98315efa3d62245a682555267208992`。同一 `:outbox` attempt 从 `1785982875004` 到 `1785982891062` 持续 16,075 ms 并进入 uncertain DLQ；其间两个 `:inbox` attempt 分别在持久化后 118／197 ms 开始，第一条在 Outbox 终态前 15,604 ms 已被领取。三轮均 `INPUT_COMPLETED/OK`、TaskGraph `COMPLETED`、Acceptance `APPROVE`，末轮 Voice 为 `APPLIED`。方向阻塞项提升为 `PASS_REAL_BLOCKING_OUTBOX_OVERLAPPED_INBOX_PROGRESS`；回复“嗯，那就好。我这边也正好闲着。”仍需人工评审，微信物理送达也没有因此变成 PASS。

## 2026-08-06 A15 OBS 派生投影写放大

对普通陪伴延迟做阶段拆分时，隔离 Host SQLite 的 `operator_query_projection_journal` 已占约 1.93 GB。OBS 正常运行的 5 秒窗口从 51,725 行增至 51,750 行，`source_payload_json` 总量增加 1,640,149 bytes；新增大行主要是约 853 KiB 的 Gateway source 和约 653 KiB 的 session projection。按该瞬时速率外推不具备长期性能结论，但足以证明派生读模型正在反复保存完整大快照，必须修复。

代码现在保留 `operator_query_projection_document.payload_json` 为普通公开 JSON，只在 immutable journal 中把超过 8 KiB 且不超过 16 MiB 的载荷写成 zlib/base64 JSON envelope，并绑定原始 byte size 与既有 source digest。历史读取和 journal rebuild 同时支持旧对象行与新 envelope；解压有 16 MiB 上限，并拒绝非法 base64、非终止 zlib stream、尾随数据、大小或摘要漂移。7 项重放／隐私／原子切换定向集成、110 项相邻 QueryProjectionStore unit 与 1 项修正后的 composition 精确端口用例均通过；首次相邻运行暴露的唯一失败是测试 fixture 未纳入生产已经显式注入的 `episodic_store`，现已同步。

`636252264` 加载后的真实 OBS PID 60936、Host 和 Nginx Monitor 均健康。30,008 ms 样本新增 46 行，其中 11 条为压缩 envelope、35 条为小 JSON，摘要错误为 0；声明原始载荷 2,996,490 bytes，实际保存 410,858 bytes，降低 86.289%。最大 Gateway source 从 893,866 bytes 压到约 79 KiB，session 从 698,586 bytes 压到约 123 KiB。脱敏报告 `obs-projection-compression-1785985583283.json` 的 SHA-256 为 `dc55ab31e384845feba3381b6265ac06f4792706300831e98c11f86f803828ce`，不包含 payload body。

该结果关闭“大载荷未压缩”，但没有关闭“每次刷新都追加历史”：压缩后 30 秒仍产生 46 条派生 journal row。独立 OBS 随后启用默认每 kind/key 每小时一个恢复检查点，检查点之间的 current document、source cursor 和 freshness 仍原子更新。第一次真实重启后的 39.105 秒窗口里，Host／OBS／Monitor 前后均为 HTTP 200，135 个文档 `updated_ts_ms`、134 个 freshness、33 个 source cursor 持续推进；但 journal 仍新增 8 行，因此判为 `FAIL`，不能越级封板。新增行精确分解为 7 条同一 Host 实例 revision lineage 和 1 条新 Scheduler TaskGraph 的必要首快照；脱敏失败报告 SHA-256 为 `ad4d8c178a66e0040b147b1c8fa6c6129be443b954fbc73bfecbdcb544a7b00c`。

修正后只有 Host `source_instance_id` 切换会强制写 lineage，同一实例 revision 进入小时检查点；历史仍足以保存全部退役实例并拒绝 replay。新文档首快照继续保留，不被误判为刷新写放大。journal rebuild 可以有界恢复到上一个检查点，且通过 state signature 拒绝未写 journal 的并发 current 变化；29 项重建／检查点／Host lineage 定向测试通过。

`59efa3714` 加载后的第二次真实复验通过。Obs PID 45628 与未重启的 Host、Nginx Monitor 在 49.084 秒窗口前后均返回 HTTP 200；192 个 current 文档的 `updated_ts_ms` 和 freshness 继续推进，31 个 source cursor 变化。journal 仅新增 1 行、6,872 bytes，经窗口前身份集合核对为新 Scheduler TaskGraph 的首个必要检查点；既存未到期文档的刷新历史为 0，同一 Host 实例 revision 历史为 0，SQLite page count 增量为 0。脱敏报告 `obs-projection-hourly-checkpoint-v2-1785987656774.json` 的 SHA-256 为 `92baff1b2117e5d95a47e534a2c908642b47be4d7db56e9f144beae663f563e4`。

因此 OBS 派生 history 的“大载荷重复保存”和“轮询逐条追加”两项均按真实进程关闭；这不等于整体延迟、长期 SQLite maintenance 或既有 1.93 GB 历史已经完成。旧历史继续只读保留，不做破坏性清理，也不对 canonical Life／Task／Gateway 数据改写。

## 2026-08-06 A15 普通聊天同步边界

OBS 检查点生效后的真实 Gateway 三轮报告为 `dl-live-1785987851661.json`，SHA-256 `7feaf1a42a919e1abf829f62f00e0d3b4c7e37d8c196ff08fb66226ead58d81a`。Host／OBS／Monitor／Gateway／Edge 五项前置探针均为 HTTP 200，三轮 TaskGraph 与 Acceptance 完成，writer probe 为零旧 authority 增量，末轮 Voice 得到 `TTS_PLAYBACK_FINISHED/APPLIED`；VTS 仍按外部授权未完成拒绝。三轮 Host 终态为 13,115／15,349／7,971 ms，因此报告只证明投影优化没有破坏链路，不能证明实时门槛通过。

持久账本时间线把主因收窄到回复后的同步 effect 链。代表轮的 provider latency 为 1,719 ms，但随后依次等待 `conversation.user_turn`、`life.conversation_turn_recorded`、`life.foreground_completed`、`host.companion_expression_enqueue` 和 `surface.reply_delivery`，到 `INPUT_COMPLETED` 又消耗约 7.7 秒。普通无副作用陪伴没有理由等全部派生投影后才让文本可见。

当前实现不删除任何 durable intent，也不恢复松散 `create_task`。Acceptance 编译器把可信 `command.route_type` 写入用户轮次；只有 `route_type=chat` 且恰好一个用户轮次、一个 Surface delivery 的图，才同步执行 `conversation.user_turn → surface.reply_delivery`。Life、foreground、expression、explicit memory 与 preference 继续保留在同一持久队列，Surface 提交后通过 app-local `AcceptanceEffectRuntime.wake()` 立即恢复；用户轮次失败时禁止越过并发送回复。无标记旧图、Plan、Skill 和控制路线仍使用原完整顺序。47 项定向 compiler／dispatcher／Runtime 合同与 1 项真实 SQLite／生产组合集成通过；集成同时确认 Gateway intake 前已有用户轮次、尚无 Life projection，后台 drain 后 Life／Expression／Preference 全部收敛。真实进程尚未加载本改动，故状态为 `CODE_REMEDIATED_REAL_RETEST_PENDING`，A15 总体仍为 `INCOMPLETE`。

`652ade828` 加载后的真实报告为 `dl-live-1785989718977.json`，SHA-256 `8170667ce02b37e31d35e30cc8c95eeb84ebde6f350ea0afff1e93bd30ecc017`。Host／OBS／Monitor／Gateway／Edge 五项探针均为 HTTP 200，三轮各有一张完成的 TaskGraph，六类 effect 最终全部 `COMMITTED` 且无重复；末轮 Voice 为物理播放终态，旧情感 writer 增量为 0，VTS 继续是外部授权等待。相对 effect 创建，三轮用户轮次分别在 526／725／422 ms 提交，Surface 在 1,217／1,499／1,694 ms 提交，Host 终态仅晚 54／67／82 ms，证明同步前缀本身已经缩短。

同一报告的绝对时间线仍判定为性能失败：连续输入到 Host effect 创建存在约 3～5 秒、由上一轮后台链重叠造成的额外等待。根因是 Dispatcher 虽会把同步 handler 放入线程池，但两个 Life handler 使用 `async __call__` 包住纯同步 SQLite 主体；Dispatcher 在线程中只创建 coroutine，实际主体随后又回到 ASGI 事件循环。修复保留 handler 的异步合同，只将 Life conversation 与 foreground 的同步主体显式交给 `asyncio.to_thread`；Expression、Preference、Memory 原本已经在线程池，不做重复架构改造。新增两个确定性事件循环响应测试，连同恢复、关系与 Universal Host Runtime 共 111 项通过。真实进程加载新提交前状态为 `ORDINARY_CHAT_BACKGROUND_EFFECT_EVENT_LOOP_OFFLOAD_REAL_RETEST_PENDING`；三句回复的人工自然度也未通过，首句“别太往心里去”需要在更广盲测中审查。

`55d60a384` 加载后的同场景复验报告为 `dl-live-1785990846080.json`，SHA-256 `33eb1871d040578ea50185ee82732c7e37589eb2763182af3dcbda04ac798a75`。五项探针继续为 200，三轮自动通过，全部六类 effect 各提交一次，末轮 Voice 到达物理播放终态，旧 writer 为零且 canonical journal 推进。第二轮发送时上一轮后台尚余 3,824 ms；它仍在发送后 1,984 ms 创建 HostCommand／TaskGraph、2,527 ms 启动 Attempt，conversation history 在上一轮后台结束前 1,098 ms 已读完。这是实际进程中的重叠推进证据，因此 Life event-loop offload 提升为 `PASS_REAL_OVERLAPPED_NEXT_TURN_PROGRESS`。

整体性能没有随之越级通过。三轮 Host latency 为 11,657／10,137／10,075 ms；前两轮分别经历 2,875+1,797 ms 与 1,500+2,327 ms 的两次模型尝试，第三轮单次模型为 1,266 ms，但远端 Gateway attempt 的 `dispatch_started_at_ms` 校正约 1,496 ms 时钟偏移后，比 Host HTTP start 早约 2,287 ms。随后远端到 Host 的 12 次 health 探针为 15～81 ms，因此该单样本只能记作 transport／进程调度抖动，不能推成稳定网络缺陷。当前性能阻断改为 `ORDINARY_CHAT_END_TO_END_LATENCY_ABOVE_GATE_FIXED_OVERHEAD_AND_PROVIDER_REPAIR`；人工评审仍为 `PENDING`。

进一步对 Surface `prompt_build_ms` 做生产代码与真实语料同条件基准，确认默认 lexical-first 路径会在每个新场景遍历约 3.4 万条安全可用台词，并逐条重建 `SurfaceOriginalQuote`；三场景首次／新查询分别耗时 1,171／676／714 ms。修复没有关闭原作语料，而是默认只保留七海本人约 3.5 千条安全台词作为运行候选、缓存解析对象，并把 `question_budget` 绑定到 few-shot 选择，避免零问句回合继续注入问句样本；无合格本地样本时直接省略风格参考，不再隐式触发本环境首次 8.5 秒且空结果的向量回退。修复后相同三场景为 458／31／36 ms，205 项 Surface 定向测试通过。该数据是代码级真实语料基准，不是完整 Gateway 延迟结论；实际服务加载新提交后的状态仍为 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

## 2026-08-06 A15 OBS Task 投影写锁公平性

`61a9b9a75` 加载后先保留原 2.72 GB 库，再在 `host-clean-61a9b9a75` 隔离根创建全新 canonical SQLite，排除历史库体积作为单一解释。OBS 开启时的三轮报告为 `dl-live-1785993815497.json`，SHA-256 `ce1892859f0f488723fc67bc6e58696ee1b06e39c6aad1ccd2254bcbfd10042d5`；Host 终态 7,296／10,197／5,437 ms。第二轮 Attempt 在 `1785993825938` 启动，Surface 在 `1785993828837` 完成，worker projection 记录 `created_ts_ms=1785993828994`，BudgetUsage 直到 `1785993831874` 才落库，相隔 2,880 ms。该 Attempt 未达到 30 秒心跳周期，故不是 heartbeat sleeper。

同库关闭 OBS 后的对照报告为 `dl-live-1785994204620.json`，SHA-256 `0fb651bebb0e06db7c946f78588fffe77c4855b14cc79239b7ff137703f7a36f`；三轮 worker projection 到后续 artifact 仅 233／229／312 ms。重新启动 OBS 后直接查询 `task_execution` current 文档，单次波次从 index 到 36 个详情的更新时间跨 3,007 ms；旧批次上限 64 会在当前 36 个图规模下连续执行全部 SQLite 写事务，等待中的 Host writer 被重复抢占。无 OBS 对照只用于定位，不能计为正式服务栈性能 PASS；第二轮远端 TTS 另有约 96.5 秒物理执行尖峰，也不混入 Host 根因结论。

修复将 Task 详情批次上限收窄为 16；缺失文档仍优先，所以新 Task 首波可见，既有 36 个详情约 45 秒轮转一次。每次派生 projection 提交后，OBS 线程显式留下 5 ms 无写锁窗口；Chat worker projection 则在线程中持久化并在 caller cancellation 时等待写入终局，避免 SQLite busy wait 冻结其他 ASGI 协程，同时不产生失联后台写。定向测试先证明 16／64 分批、17 次公平让锁以及阻塞 projection 时事件循环仍可推进。真实 Host／OBS 新 SHA 复验完成前，本项为 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`ead903f66` 加载后的正式复验报告为 `dl-live-1785995815718.json`，SHA-256 `7415951164753cd00c68d55b40b35167e30feefe93d02503f259dee34f2f06ce`。Host、OBS、Nginx Monitor、Gateway、远端 Edge 与三个真实 Docker 容器同时在线；OBS daemon 连续成功且无失败波次。三轮 worker projection 到 `WORKER_USAGE` 落盘分别为 187／94／49 ms，远低于修复前 2,880 ms；到 artifact 为 1,054／269／246 ms。current 文档实际轮转的详情批次最大为 16，index 加详情最大为 17，证明配置没有只停留在单测。

三轮 TaskGraph 分别为 4,899／4,903／4,736 ms，Host 终态为 7,040／7,159／6,509 ms；自动陪伴结构和旧 writer 清零通过，末轮远端 Voice 到达 `TTS_PLAYBACK_FINISHED/APPLIED`。本项因此提升为 `PASS_REAL_FULL_STACK_WRITER_FAIRNESS`。整体实时 Gate 仍失败：第二轮有一次模型语义修复，首轮 artifact 冷态尾部仍为 1,054 ms，VTS 为外部连接拒绝，人工自然度评审仍为 `PENDING`；A15 总体继续为 `INCOMPLETE`。

## 2026-08-06 A15 本地实时对话首句拆分

在五端服务与 Docker 保持在线时直接请求本地 `/v1/chat`，三轮首个可见 `RENDER` 分别约为 4.52／5.76／2.98 秒；另一个带公开 timing trace 的样本为 3.80 秒。Surface 自身的真实 DeepSeek 流约占 1.6～1.7 秒，而 Cortex 的 `NO_RECALL` 检索阶段仍等待 0.54～0.56 秒。短暂停止 OBS 后该等待仍为 0.54 秒，说明它不是上一项派生 writer 饥饿的残留。

第一轮代码审查把整个等待归因于 `_recent_explicit_memory_facts()` 过强。`0165e95f9` 确实把该兼容 helper 移入 `plan.should_recall` 分支，并以端口断言固定 `NO_RECALL` 的计划边界；但 helper 原实现会先用 `_looks_like_explicit_memory_content_query()` 拒绝无关文本，再决定是否访问 ConversationHistory。加载新提交后的同句样本中，检索由旧样本 561 ms 降至 194 ms，首句却因 provider 抖动由 2.64 秒变为 2.76 秒，所以该变更只能记为安全的合同简化，不能记成已证明的 0.5 秒性能修复。

继续逐调用点审查发现，`_maybe_apply_lightnovel_deep_review()` 会在每个回合先调用 `build_user_preference_pack()` 读取最近推荐上下文，之后才判断用户是否真的在问轻小说。`7c454b6fb` 增加与完整判定同源的消息级必要条件：无“哪本／好看／详情／简介”等选择或细节语义时直接返回；只有可能命中的消息才加载推荐上下文和持久化模块。实际深读复用已加载的 packet，不再二次读取偏好。36 项 reviewer／retrieval 单测分别证明普通陪伴读取次数为 0、指代式轻小说追问读取恰好一次且仍产出深读事实；加上文档 Gate 共 60 项通过。

新提交加载到原 A15 隔离根后，Host PID 4132、OBS PID 62536、Nginx Monitor PID 60772 与 Neo4j／Chroma／Redis 同时在线。三条相同 `NO_RECALL` 文本的 `retrieval_wait_ms` 为 124／100／104，首个 `RENDER` 为 3,046／2,612／3,147 ms；Surface 为 1,361／1,741／2,047 ms，真实 `deepseek-v4-flash` 首 token 为 1,185／1,569／1,775 ms。无关领域钩子子项因此提升为 `PASS_REAL_UNRELATED_DOMAIN_HOOK_SKIPPED`，脱敏收据 SHA-256 为 `25f23ac04b9a117a8ceb63b3bb19499dcea67420259f87f052f873be1cba5aff`。该范围 PASS 不把首句差值全部归因于本切片，也不代表整体实时通过。

当前拆分还显示两类独立尾部：同轮两条情绪 `MAY_RECALL` 的本地 embedding 与多路召回为 514／568 ms，首句为 3,269／2,768 ms；DeepSeek 首 token 在本轮五条样本间为 1,185～1,775 ms，历史样本可到约 2.2 秒。二者均未关闭，首句实时 Gate 和人工人感继续为 `INCOMPLETE`。

对两个 `MAY_RECALL` request 的 canonical `retrieval_event_log` 继续拆分，真实 route ledger 分别只记录 125／63 ms；同环境独立暖态基准中，Qwen3-Embedding-0.6B 的第二、三次单句编码为 91.1／91.7 ms。审查 composition 后确认每次 `_run_retrieval()` 都调用新 factory，`RecallFusionServiceContract` 又会新建 `PersistentChromaClient`；对同一 Docker Chroma 的暖态 client 构造五次为 63.9～73.5 ms，首个进程冷构造为 1,161 ms。Host 已拥有 canonical `EpisodicStore.chroma`，这次重复 ownership 没有语义价值。

`f0aa50a58` 在 `AgentRuntimeApplicationScope` 内以锁保护一个 lifespan-bound RecallFusionService，并注入现有 EpisodicStore Chroma client；并发请求共享无状态服务，而连接仍由 Host authority owner 统一关闭。定向测试重复调用 factory 两次，断言 builder 只执行一次且拿到同一 client；生命周期、召回和文档回归共 104 项通过。

重启后的 Host PID 3500、OBS PID 12432、Monitor PID 52800 与三个 Docker 依赖同时在线。相同情绪文本三次 `MAY_RECALL` 的 `retrieval_wait_ms` 为 316／351／508，route ledger 为 74／91／79 ms；修复前两条检索为 514／568 ms，中位数从 541 ms 降到 351 ms，下降约 35%。一条 `NO_RECALL` 对照为 118 ms。服务／client 重建子项因此提升为 `PASS_REAL_APP_LOCAL_RECALL_SERVICE_REUSE`，收据 SHA-256 为 `e1c8a0f9110be096dbb8c2f29fec1a945dd52482b009a805f30f6c85f2fcafd8`。首句为 2,964～4,712 ms且仍有 508 ms 检索尾部，所以整体实时结论保持 `INCOMPLETE`。

原 trace 只记录 Cortex 等待 retrieval task 的总时长，无法区分 embedding 调度、app-local service resolve、route 执行和两个 domain reviewer；通用 preference pack 又发生在该计时之后。新切片在原 COMMIT trace 内补齐这些整数耗时，并记录整个 context pipeline；没有新增表、journal 或普通回合写事务。通用 evidence reviewer 的 `observability_persist` import 同时后移到 packet-aware predicate 命中之后。78 项非重复 retrieval／composition／HTTP 集成与文档 Gate 通过，真实进程加载前只标 `CODE_COMPLETE_REAL_RETEST_PENDING`。

加载 `d1777f109` 后的真实 HTTP 流进一步否定了“当前首句主要被召回拖慢”的判断。一条安静陪伴消息在 802 ms 已进入 Cortex，但直到 26,777 ms 才出现首个 RENDER；COMMIT 中 provider `first_token_ms=24,999`、模型为 `deepseek-v4-pro`，而 context pipeline 为 613 ms、retrieval 为 468 ms。根因是高歧义双候选让 buffered Surface 调用方把 `realtime_eligible` 置为 false，进而从 `CHAT_SURFACE_REALTIME／B` 漂移到 `CHAT_SURFACE／A`。修复只取消这项错误耦合，不取消双候选、边界 Gate 或 Task 路线的质量模型；110 项本地代码、集成和文档 Gate 通过。

`09e828149` 重启 Host／OBS／Monitor 后，三个 Docker 依赖与既有 Gateway／远端 Edge 保持在线。三条真实样本的模型车道均为 `CHAT_SURFACE_REALTIME／B／realtime_companion`，provider／model 均为 `deepseek／deepseek-v4-flash`；首 token 为 2,157／1,834／1,981 ms，首个 RENDER 为 4,263／3,360／3,633 ms。相对同句错误 Pro 样本，首 token 降低 22,842 ms。该子项记为 `PASS_REAL_BUFFERED_COMPANION_TIER_B`，收据 SHA-256 为 `d383bf20246ed5f701ce72b9a66572bcf9eb9de0a23c62c9b86d683be2a4b9b0`。两条真实双候选均完成 `PAIRED_SELECTION`，但 `selected_passed=false`；首句仍高于体验门槛，因此整体实时和人感状态保持 `INCOMPLETE`。

## 2026-08-06 A15 三类情感连续性失败与代码修复

在相同实时模型车道上继续执行隐晦低落／空间边界／喜忧并存三类真实 Gateway 多轮 cohort，共 9 个 accepted turn；TaskGraph、Acceptance 和远端 Voice 主链可达，旧情感 writer 增量为 0。报告 SHA-256 为 `c19bc40c293ca6bf9c931178258cd2284d0ff4374e0f3e4ead90b269e085578a`。自动规则仅 1/3 通过，人工复核 3/3 均失败：末轮分别把“没事”误判成“一切正常”、以“想我了可得自己开口”反向要求用户、只说“恭喜”而丢失此前的不踏实。该结果将 `digital_life_human_likeness` 从未运行纠正为真实失败，不能被 TaskGraph 成功或 Voice 完成覆盖。

持久证据显示 `conversation_turn.turn_index` 始终正确交替，但 Gateway／Host／Edge 的 `ts_ms` 存在秒级偏差；近期历史原先按 `ts_ms` 排序，导致同角色消息相邻并在消息规范化时被去重。canonical affect 快照其实仍保存 CARE／CONCERN 或 SHARED_JOY＋CAUTION 的未解决 episode，但当前消息无新 appraisal 时，Planner 会有意丢弃旧 appraisal，于是“没事”和“拿到 offer”失去跨轮语义。边界候选评分也已经报告 counter-pressure，却因不属于 hard fail 仍可入选。

代码修复将近期对话改为按 canonical `turn_index` 排序；Fusion 仅在仍活跃的未解决 episode 与窄标记共同匹配时生成两个有界 continuity enum，Planner 冻结 enum 并加入相应事实／禁区；Surface 在 `GIVE_SPACE` 下硬拒绝反向联系要求。修复没有新增数据库表、journal、普通对话写入或模型调用。56 项直接单元测试与 267 项相邻 conversation／Surface／集成测试通过；真实 Host 尚未加载该提交，同一 cohort 复验前只记为 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`6160c4564` 加载后重跑同一 9 轮，报告 SHA-256 为 `58b871de4dcc9efee488f441f674f9affc87822e1174237a5af113c0b4d07215`。所有轮次进入真实 TaskGraph，旧 writer 仍为 0；低落和混合末轮 trace 的 ConversationHistory 均为 4 turns、`deduped_same_role_count=0`，continuity enum 分别为 `MINIMIZED_UNRESOLVED_AFFECT`／`MIXED_AFFECT_RESULT_CONTINUATION`，CompanionAct 分别为 `LISTEN`／`HOLD_COMPLEXITY`。后续最终 prompt 检查证明，这只验证一条 history 路径和截断前 Planner，不能证明 Surface prompt 已按轮序保留约束。

体验仍失败：低落末轮仍回复“嗯，那就好。我这边也一切正常”，混合末轮回复“恭喜你！这下可以松口气了吧”。空间边界正文“知道了。那我就不老打扰你了，有事再叫我”人工可接受且 `selected_passed=true`，但该场景三个 turn 的 `edge_idle_before=false`，所以自动 verdict 仍失败。第二层修复仅在 canonical continuity marker 已存在时新增 `UNRESOLVED_AFFECT_FALSE_RESOLUTION` 与 `MIXED_AFFECT_CONTINUITY_DROPPED`，命中后走既有一次 semantic repair；141 项定向测试通过，真实再测前总体维持失败。

`eb2f68d29` 第二次 9 轮报告 SHA-256 为 `b813705664942dc0fa9524cf915873e4891b5f6aab08d71fd218f9b7e7a62ebb`。低落仍输出“那就好”，混合仍只庆祝；两者 model attempt 都只有一次成功且 hard-gate observation 为空，证明 marker Gate 没有进入真实 compiled request。空间边界则真实出现首次 `SEMANTIC_RETRY_REQUESTED`、retry reason `BOUNDARY_COUNTER_PRESSURE`，第二次生成安全正文，说明通用 hard-gate 主链本身正常。

最终 `surface_task_control` 虽含 20 多条事实，但 `ReplyControlPacket` 只保留原列表前 8 条；最近对话、陪伴动作和 continuity 位于后部，被泛化身份／后台状态挤掉。Surface 的 `_surface_recent_conversation_window_facts()` 又使用另一只 `RuntimeReadRepository`，其中仍按 `ts_ms` 排序，所以最终 prompt 继续出现“用户／用户／Natsume／Natsume”。当前代码统一该 Repository 为 `turn_index`，在 8 条原上限内按必要事实／最近对话／情感约束和已完成的子代理结果优先，并新增 typed continuity control；259 项 Surface／Delegate 主链回归及 163 项情感、历史与恢复回归通过，第三次真实复验前只标 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`4127752f0` 加载后的第三次同 cohort 报告 SHA-256 为 `a818e4cbaa4f190c05f23d1cbe6396d71618caacc771c7ac334aa93b4353a6bf`。9 轮真实 webhook 都进入 HostCommand／TaskGraph 并被 Acceptance 接受，canonical affect／relationship／life／delivery 表继续前进，8 个 legacy writer 表增量均为 0。三条末轮 Task artifact 的最近对话分别保持 `用户 → Natsume → 用户` 或 `用户 → Natsume → 用户 → Natsume`；packet trace 的 fact count 均为 8，低落和混合分别携带 `MINIMIZED_UNRESOLVED_AFFECT` 与 `MIXED_AFFECT_RESULT_CONTINUATION`。

末轮正文分别为“那就不说那些了，我在这儿陪着你就好”、“你安静的时候，我不打扰你”和“开心归开心，那点不踏实我也记得”。三者都在首次真实 DeepSeek attempt 成功，没有依赖 semantic repair 才过线；窄范围单人复核为 3/3。自动结果为 2/3，混合场景仅因三轮 `EDGE_NOT_IDLE` 失败。测试结束后的远端 health 显示 TTS persistent worker `ERROR`（ready／synth_result timeout），VTS 仍等待认证；因此本证据只把 canonical 轮序、typed continuity 和文本输出提升为 scope-bound PASS，Voice/Avatar、48 条三人盲测和整体 A15 仍未通过。

远端资源快照进一步定位到十多个 `GPT-SoVITS...runtime\python.exe` 子进程：单进程 Working Set 约 1.2～2.2GB，目标机只剩约 2.5GB 可用内存，RTX 3070 Ti 8GB 显存已使用约 7.9GB。代码审查确认 synthesis timeout 会关闭 worker，但初始 `ready` timeout 发生在 adapter 交给 `SoVITSModelManager` 之前，异常路径只丢弃局部变量而未调用 `close()`；每次重试因此留下一个模型进程。修复后 `_PersistentProcessSoVITSAdapter.start()` 对启动、READY 消息和失败响应使用同一所有权边界，任一异常都同步关闭子进程和 stderr handle；120 项 TTS、播放队列、Voice pipeline 与启动脚本回归通过。尚未部署远端和执行物理播放，状态只能是 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

## 2026-08-06 A15 远端 TTS worker 生命周期与物理播放复验

远端精确清点为 17 个 GPT-SoVITS runtime；只终止该可执行路径后，worker 归零，系统可用内存约从 2.5GB 恢复到 24.7GB，显存占用约从 7.9GB 降到 675MB。修复后的 `tts_engine.py` 同步到实际运行副本 `D:\Natsume_Ai_acceptance_d7e0cfba8`，Edge PID 30196 启动约 18.6 秒后 TTS 进入 `READY`，只创建一个模型 worker PID 34580。第一次重启后 Voice 请求在 Host→Edge session 轮换期间以 `EMBODIMENT_COMMAND_EXPIRED` 安全拒绝；它没有进入合成，不能记作 TTS 失败或成功。Host 持久 lease 随后显示 Edge session fencing token 2、runtime fencing token 14，且持续续期。

在新 session 上使用全新 effect 重跑 `implicit_low_mood:1` 三轮真实 Gateway→Host→DeepSeek→TaskGraph→Edge 链路，三轮 webhook、TaskGraph、Acceptance 均完成，legacy affect writer 增量为 0；最终 `voice.speak` 为 `APPLIED`，terminal receipt 为 `TTS_PLAYBACK_FINISHED`，`voice.release` 也为 `APPLIED`。Avatar transition 仍按真实外部状态以 `VTS_CONNECTION_FAILED` 拒绝，没有被 Voice 成功覆盖。报告 SHA-256 为 `3fbd674e9f828a67f434f2cfef5acb950dbf53643be03c0a86fcd35f206f5f3a`，自动状态为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`，末句“嗯，那就先这样待着吧。”仍保留人工评审。

随后在不停止主 Edge 的独立真实进程探针中将 READY timeout 强制为 5 秒。探针于 8.234 秒返回预期 `TimeoutError`，adapter 持有的子进程已释放；再次清点仍只有原 worker PID 34580，可用内存约 23.6GB，显存 2861/8192MB。脱敏生命周期收据 SHA-256 为 `3e8024afd135f69d6e6dac693e71539100481c9b584b040d9f59a2b37ce3f607`。本项由 `CODE_REMEDIATED_REAL_RETEST_PENDING` 提升为 `PASS_REAL_REMOTE_SINGLE_WORKER_PHYSICAL_PLAYBACK_AND_TIMEOUT_RECLAIM`；VTS、声学 barge-in、48 条三人盲测和整体 A15 继续为未完成。

## 2026-08-06 A15 PlanningSearch Research 双臂提示绑定

对当前隔离 A15 SQLite 直接清点后，Research registration／run／arm／terminal receipt、Code workload／run／arm／receipt、Pentest calibration 与三域 cohort registration／member／report／promotion 表均为 0。随后完整机械回归执行 38 个 PlanningSearch 测试文件，结果为 300 passed、1 skipped；唯一 skip 是 Windows `WinError 1314` 符号链接权限，不是搜索收益样本。该结果只证明合同、ledger、编译、验证和恢复机制可运行，不能把三域收益提升为 PASS。

真实 Research 预注册还暴露一个被 fake receipt 遮住的生产缺陷：`ResearchExecutionProfileV1` 只有一个共享 `prompt_digest`，paired runner 又要求 baseline 和 selected receipt 都等于它；但 PlanningSearch 改变 source set 后，两只 arm 的真实渲染提示必然不同。新规格因此在 `ResearchArmPlanV1` 上分别持久绑定实际 arm prompt digest，执行器按 arm 精确校验；旧 0107 RunSpec 缺字段时继续回落到历史共享 digest，保证 canonical JSON 与 exact replay 不漂移。codec、operation replay 和 SQLite reopen 均覆盖该字段，25 项相邻回归通过。

该切片完成时状态为 `CODE_REMEDIATED_REAL_24_PAIR_PENDING`；后续真实执行结果见本节末尾，失败样本和预检样本均保留在 append-only ledger。

## 2026-08-06 A15 DeepSeek Research 成本证明

当前官方 DeepSeek V4 API 返回 `prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`、`prompt_tokens`、`completion_tokens` 和 `total_tokens`，但不直接返回本次货币金额。D20c 原适配器要求 `model_cost_micros` 和 `tool_call_count`，所以此前普通成功调用仍不能成为 Research benefit evidence。

生产 provider adapter 现只在 HTTPS 官方 `api.deepseek.com`、模型为 `deepseek-v4-flash`／`deepseek-v4-pro`、cache hit + miss 与 prompt token 完全相等时，应用版本化 `deepseek-v4-usd-2026-07-24` 价目并向上取整到 micro-USD；价目来源为 `https://api-docs.deepseek.com/quick_start/pricing`。请求协议没有暴露 tools 时，`tool_call_count=0` 是请求结构事实。代理地址、未知模型、缺字段或计数不一致都不会得到成本值。

在当前 A15 SQLite、真实 production composition 与 canonical `ModelAttemptService` 上执行一次全新 Research 探针，结果为 `deepseek-v4-flash`、51 total tokens、9 micro-USD、828 ms provider latency、844 ms total latency、外部 transport identity 有效、工具调用 0，最终 model receipt 的 `benefit_eligible=true`。该探针只关闭成本／usage attestation 阻断，不是 Research 配对或收益结果。

## 2026-08-06 A15 Research 隐藏质量评审边界

paired terminal ledger 不能保存回答正文，但真实收益又必须评估内容。当前 runner 因此只在新鲜双臂完成的同一进程里返回不可序列化的 `ResearchTransientArmOutputV1`；terminal replay 的 transient 列表固定为空，不能从摘要恢复正文或补签质量证据。独立隐藏评审不接收 arm 标签，使用统一 rubric 和另一条 canonical 外部 ModelAttempt，最终只把被评回答摘要、独立 execution／attempt、prompt digest、usage、cost、transport、分数和严重错误数写入 receipt。

候选正文 JSON 形状漂移、评审输出额外字段、越界分数和非外部 attestation 都会在 cohort 写入前失败。paired 恢复、历史重放、质量 receipt 和隐藏输入的 22 项定向测试通过。该切片当时只关闭正文隐私与评审身份边界；后续真实收益结果见下文。

## 2026-08-06 A15 Research 固定队列准备

仓库现冻结 24 个 workload，运行时恢复、存储持久化与模型证据各 8 个；每个主题由本地 primary、官方 Web independent 和信息不完整的 secondary 三份短来源组成。expected points 只存在于隐藏评审输入，不进入公开 planning snapshot、候选 payload 或执行 arm。baseline 与 selected 并非手写两套执行器：前者通过同一 `GoalPlanningService` 的 `MCTSMode.NEVER` 选出 secondary quick answer，后者通过 AUTO 条件 L3 选出 primary＋independent 路径。

完整 dry-run 结果为 24/24 search arm 使用 MCTS、24/24 两臂 prompt digest 不同、主题数 3、来源种类同时包含 `LOCAL_FILE`／`WEB`、外部请求 0。语料篡改会在 planning 前因 byte count／digest 漂移失败。相关 14 项 runner、隐藏评审和 paired recovery 回归通过。该步骤本身只证明固定实验可启动，后续真实结果如下。

## 2026-08-06 A15 Research 真实收益队列

在真实 A15 SQLite 与 production composition 上，先以 `preflight-1c9c2b2bd-v1` 完成单题双臂、独立生成请求和两次隐藏质量评审。预检状态为 `PASS_PREFLIGHT_ONLY`，耗时 6,943 ms，文件 SHA-256 为 `03b72f857a57a94098269e1537879154ccb3dce9471189b88844bc6193a7a0bb`。随后以新 namespace `full-1c9c2b2bd-v1` 完整执行 24 题；48 个 arm generation 与对应隐藏评审均使用真实 `deepseek/deepseek-v4-flash`、独立 execution identity、非缓存外部 attestation 和 canonical ModelAttempt audit。

最终 cohort 状态为 `PASS`：质量均值提升 `0.85416667`，paired bootstrap 95% 下界 `0.75`；baseline／selected citation validity 均为 `1.0`，严重政策错误与冲突错误均为 `0`；总成本比 `1.32215978`，p95 延迟比 `0.98756943`。生成请求成本合计 5,067 micro-USD，隐藏评审合计 6,502 micro-USD。报告文件 SHA-256 为 `eb17699c0f85b2b9b680f02f5ce1c716726e2f15f2bfd8b72cf437f6c7277894`，run receipt digest 为 `6313ff8e241266efd9dfe98526f24b7d2da5fc664cc9f4fd646290f7b75e7497`。

重开 SQLite 后，权威表中有 24 条 registration、25 条 run spec、50 条 arm execution、25 条 terminal receipt、1 条 cohort registration、24 条 member evidence 和 1 条 cohort report。最终 report identity 为 `cohort_id=a15-research-benefit`、`cohort_version=2026-08-05-v1`、`spec_digest=829a05f75d89a5f6b25f4b538918010aefb3638cbe9199a21ec57024550cb8e6`；`benefit_eligible=true`、`proposal_eligible=true`，但 `production_influence_enabled=false`。冻结来源正文在 durable evidence 中命中为 0，数据库只保存摘要和机械证据。

因此 Research 域由 `UNPROVEN` 提升为 `PASS_REAL_24_PAIRED_EXTERNAL`，且满足“至少一个非 Pentest 域通过后可提出 L3 promotion proposal”的必要条件；本次没有自动 promotion。Code 20 题与 Pentest 30 次仍未执行，PlanningSearch 三域总状态只能是 `PARTIAL_RESEARCH_PASS_CODE_PENTEST_PENDING`。

## 2026-08-06 A15 Code 固定收益队列准备

仓库新增 20 个固定小型 Python 修复任务。配置只冻结任务目标、错误次级证据、权威证据和隐藏输入输出；runner 在 acceptance 忽略目录中生成确定性的干净 Git 源提交。外部模型只看到当前源码、任务目标和 PlanningSearch 选中的证据，不接收隐藏 cases。baseline 使用同一 Code domain 的 `MCTSMode.NEVER`，selected 使用 AUTO 条件 L3；完整 dry-run 得到 20/20 baseline 未进入 safe patch、20/20 selected 触发 MCTS 并选择 safe patch、20/20 虚拟状态身份一致，外部请求 0、worktree 执行 0。

真实 runner 会在任何外部调用前把 workload 与 paired RunSpec 预注册到现有 Code ledger；owner 先生成随机种子并逐题冻结 arm 顺序，再以同一 provider/model/budget 发起两次独立 canonical ModelAttempt。模型只返回目标文件的完整 JSON 源码，结果在进程内应用到两个全新 detached worktree；每只 arm 分别执行隐藏行为 pytest 和 AST 静态 pytest。受控 receipt 只保存 argv/output digest、退出码、耗时、diff、外部 usage/cost/transport 与清理证明，不持久模型源码或隐藏用例正文。终态 replay 没有模型正文时明确要求新 namespace，不会重复外部调用冒充恢复。

当前状态为 `CODE_COMPLETE_REAL_PREFLIGHT_PENDING`。队列、随机执行顺序、sandbox、ledger、cohort evaluator、SQLite 集成和 prompt registry 相邻回归共 50 项通过；真实单题和 20 题尚未执行，因此 Code 收益仍为 `UNPROVEN`，PlanningSearch 三域总状态不变。

`e3094cfbd` 的真实预检保留了三次互不覆盖的运行。v1 与 v2 都是 selected 请求成功、baseline 在 8,000 ms 发生 `PROVIDER_TIMEOUT`，分别以 `FAIL` 写入外层收据和 canonical model audit；v3 通过隔离进程显式设为 30,000 ms 后完成两臂，baseline 隐藏测试失败、selected 隐藏测试通过，静态检查均由受控 worktree 执行，状态为 `PASS_PREFLIGHT_ONLY`。v3 文件 SHA-256 为 `147da7fffe5937379ed8da80c53127ac22a1a4dcfa0432ca584a807cfaa269a4`。

该结果揭示的不是模型凭据故障，而是 Code benchmark 误用了 `RESEARCH` scene，并继承面向短请求的全局 8 秒上限。修复后 Code 请求使用独立 `CODE_BENCHMARK` scene；runner 显式拥有 30 秒默认 route timeout，并把 scene、route timeout 和 90 秒 request deadline 纳入 execution profile digest 与脱敏收据。Research 的默认 scene、metadata 和行为保持不变；57 项 Code／attestation／ledger／SQLite／文档回归与修复后 20/20 dry-run 通过。由于修复改变了代码与 profile identity，v3 只作为缺陷复现／修复依据；最终 20 题必须在修复后的新 commit SHA 上重新预检和执行。

路由修复提交 `926d643c6` 的新单题预检不再需要手工环境覆盖：实际 scene 为 `CODE_BENCHMARK`、timeout 为 30,000 ms，随机顺序 baseline→selected；baseline 隐藏测试失败、selected 通过，两臂静态检查通过，状态 `PASS_PREFLIGHT_ONLY`。文件 SHA-256 为 `63559c45967520bddc45ac98104b24cb34e4af4c0fb945efe7c9bcd0214a66f1`。

随后首轮 20 题完整运行没有被包装成通过。前 5 题都得到 baseline 失败、selected 通过；第 6 题 baseline 在 30,015 ms 触发真实 `PROVIDER_TIMEOUT`，完成数停在 5/20，状态 `FAIL`，文件 SHA-256 为 `272638f91ffbb5b10a449ce725e25750b6de3ab80a7aec383b917641b5ecce8d`。已完成成员、失败 RunSpec 和 canonical model audit 全部保留。

失败进一步暴露新 Code prompt 没有 production generation profile，因而仍继承 provider 宽泛默认，简单错误规格也可能耗尽隐藏推理时间。固定队列升级为 `2026-08-06-v2`；Code prompt 现冻结 temperature 0、top-p 0.8、max tokens 600、`thinking_mode=disabled`，并把 generation 参数纳入 `a15-code-full-file-json-v2` execution profile digest。98 项相邻回归和 v2 20/20 dry-run 通过；配置摘要为 `170bc196add828676c00b6af79d98153dbc09f48c67227be78b0952cfa0d8cea`，profile digest 为 `cdedce854508661e46540b011eb9c7005519eb23cf89f7217e866537cfde5302`。v2 必须使用新 commit、新 namespace 和完整 20 题重新预注册，禁止复用或挑选 v1 的 5 个成功成员。

v2 第一次新 SHA 预检在任何外部模型调用前 fail-closed：Code ledger 发现同一 `workload_id` 正被绑定到新的 cohort/config evidence，返回 `CODE_BENCHMARK_REPLAY_DRIFT`。根因是只升级了 cohort version，却没有升级 durable workload registration identity。修复后 ledger identity 使用 `<固定题号>@<cohort_version>`，workload ref 与 baseline ref 也包含 cohort version；公共题号仍由配置保留，但 v1/v2 不再争用同一写身份。该失败没有模型成本，不删除也不改写原 ledger 行。

version-bound identity 修复后的第二次 v2 预检完成了两只真实 DeepSeek 请求，latency 分别为 922 ms 与 625 ms，说明 non-thinking profile 已实际消除此前的长推理；但进入 sandbox 时 Windows bare clone 因长 acceptance 根目录、run namespace 与 run ID 叠加超过路径限制而失败。失败收据 SHA-256 为 `376e1a62822cbaabbfe8213d1823bae7147119e91601d5633f9897aade8ee2ae`，两条 succeeded model audit 不复用。修复将 runtime directory 和 filesystem-facing run ID 改为固定长度 SHA-256 前缀；完整 namespace、commit、config、workload 仍保留在外层 receipt 与 ledger digest 中，不用可读长路径承担语义身份。32 项定向回归与 20/20 dry-run 已通过，仍需深目录真实预检关闭该问题。

短路径修复后的 v2 第三次预检成功完成了两只模型请求、两个 fresh worktree、隐藏测试、静态测试与清理，证明 Windows 路径缺陷关闭；但 baseline 和 selected 隐藏测试均为 true。报告 SHA-256 为 `41a6584882ad883c064555b9cc71982bc52df930bb863336c656dfc53424e71c`。原因是 objective 自己写出“整数绝对值语义”，模型无需 selected evidence 也能猜出正确实现；这是 benchmark 判别力失败，不能用 `PASS_PREFLIGHT_ONLY` 标签冒充搜索收益。

固定队列因此升级为 `2026-08-06-v3`，配置摘要为 `b1c1e97eba049c57c70e54f64528d143896df7ae204d7b4328c003a3f65f75b7`。20 个 objective 统一为中性合同，只要求依据本轮选中证据实现，并明确禁止根据题号或常识猜测；正确业务语义只存在于 selected evidence 和模型不可见的隐藏 cases。loader 会对任何 descriptive objective 漂移 fail-closed，33 项定向回归与 20/20 dry-run 通过；v2 两臂都通过的收据永久保留但不计收益。

## 2026-08-06 A15 Code v3 真实收益通过

`3eaa5486b` 的 v3 单题预检重新得到有效判别：baseline 隐藏测试失败、selected 通过，两臂静态检查通过；DeepSeek latency 为 844／811 ms，文件 SHA-256 为 `224c24147f1daafe608d2df2878fa58f23a801eac291df634d27c5356bcbe544`。随后以新 namespace 完整重跑 20 题，没有复用 v1/v2 成员。

完整结果为 20/20 baseline 隐藏测试失败、20/20 selected 通过；baseline／selected pass rate 为 0／1，提升 1.0，静态检查率均为 1.0，mean diff-risk 均为 0.1。模型 Token 总数为 5,254／6,622，成本为 493／783 micro-USD；mean latency 为 889.55／1,020.8 ms，最大值为 1,358／1,375 ms。完整运行耗时 113,520 ms，满足通过率提升路线，成本上升不影响该互斥门槛。最终状态 `PASS`、`benefit_eligible=true`、`proposal_eligible=true`、`production_influence_enabled=false`。

文件 SHA-256 为 `9c9938f15d303f5699debc60af5d7ebf09b417f70b4fd2824e8115cf12062170`，自校验 receipt digest 为 `81a72b602aceda2a8c020092ed0daa55d964182050a26416915b33607b92058f`，cohort spec digest 为 `16a8c8ea0f649d4b79622b14b404111cfa11dc983276f6baffeb0309e451fe92`，report digest 为 `978dff4352ac088cfa94ec0688a8fd238a1a44faf6b8ecec4806d893f8bd4a6f`。

SQLite reopen 得到 20 workload registration、21 run spec、42 arm execution、21 terminal receipt、21 cleanup receipt、1 cohort registration、20 member evidence 和 1 report；完整 cohort 的 40 个模型 request 均各有一条 started 与 succeeded audit。private bare 与 worktree 目录均为空，protected source Git 状态干净；registration、terminal 和 member JSON 中 `权威规格` 命中为 0，外层文件中 selected evidence 与 hidden cases 命中为 0。Code 域因此提升为 `PASS_REAL_20_PAIRED_EXTERNAL`，PlanningSearch 总状态为 `PARTIAL_RESEARCH_CODE_PASS_PENTEST_PENDING`。

## 2026-08-06 A15 数字生命文本队列计时边界修正

在 48 条文本体验队列前的三轮真实预检中，Gateway→Host→DeepSeek→TaskGraph 均完成，且 canonical affect writer 前进、legacy writer 增量为 0；但脚本总耗时 176.3 秒。其中末轮在未要求 Voice 的情况下仍轮询 physical Expression receipt 90 秒，并且每轮都会要求远端 Edge 处于 Voice idle。这个时间不属于文本响应，也会让一条纯文本体验用例因远端设备繁忙误判失败。

runner 现将三个边界显式分离：文本模式不等待 Edge，只对 Expression storage 做一次即时快照；`--require-voice` 模式才等待物理 Voice/Avatar；外部微信客户端投递不由该 runner 推断。与此同时，自动通过条件被收紧为必须从 canonical Acceptance Effect 读到 user turn 和 `surface.reply_delivery` 均为 `COMMITTED`，并取得真实 `gateway-outbox://` receipt。报告新增 Gateway HTTP、Host terminal、TaskGraph、模型调用和最小 Acceptance prefix 的逐段耗时，因此 TaskGraph 完成不能再冒充消息已进入 Gateway durable outbox。每次 run 还为各轨迹生成新的 sender/relationship subject，轨迹内保持稳定，预检和旧 cohort 不再把历史关系状态带入新盲测。

第一次修正后真实预检在 54.8 秒内完成三轮，文本结构与 Gateway durable-outbox 3/3 通过；逐轮 Gateway→Host terminal 为 20,523／13,281／17,421 ms，TaskGraph 为 14,743／8,440／6,066 ms，Surface commit 为 3,323／2,372／7,266 ms。旧 runner 的 90 秒末轮 Expression 等待已被消除。报告最终只因快照时 `relationship_life_event_v1` 尚未前进而失败；稍后重开同一 SQLite 已从 181 增至 182，证明这是可见回复后的 background convergence，而非 relationship writer 缺失。

runner 因此新增最多 30 秒、单列计时的 cohort 后 canonical writer 收敛等待。它只允许等待 Affect／Relationship 尚未前进；旧 writer 变化、表缺失等失败立即终止，且该时长不进入 turn latency。24 项 runner 定向单测通过后状态仍为 `CODE_REMEDIATED_REAL_RETEST_PENDING`；需要在干净 Host/Gateway acceptance 数据根复验，当前复用多轮的 Host DB 仍有 48 条历史 PENDING effect，不能作为性能基线。48 条文本队列及三人盲评尚未完成。Pentest、渗透和网络安全专项按用户范围明确记为 `NOT_RUN_USER_SKIPPED`，不得写成 PASS，也不阻断其余非安全验收继续执行。

## 2026-08-06 A15 Edge 事件写压力修复

换用全新 Host SQLite 与 Gateway 数据根后的三轮真实文本预检已达到 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`：canonical affect 增加 8、relationship 增加 3、Life event 增加 16、Gateway durable outbox 为 3/3，legacy writer 增量为 0；报告 SHA-256 为 `e91cd984414c90aaef2277cf9cb0f13461dc3cb8e841bff15ac4a33947385170`。但 Gateway→Host terminal 仍分别耗时 14,917／10,046／23,699 ms，明显不是模型本身造成；单轮 profile 也显示 7,330 ms 总时延中 provider 仅 1,922 ms。

运行期采样定位到远端 Edge 重连后向新 Host 重放了 11,868 条 `ASR_CHUNK`。旧路径把每条 chunk 写入 `voice_event_log`，再同步派生 Life Memory episode/evidence，多条 asyncio worker 因 SQLite 写事务互相等待，Host 与 OBS 日志出现 `database is locked`。这既是普通对话延迟热点，也违反“原始／分块语音不进入长期情感记忆”的边界。

本次窄修复保留最终 ASR 的实时桥接语义，但不持久化 `ASR_CHUNK`；低频语音终态仍写 `voice_event_log`，所有 voice operational event 停止派生 Life Memory。Affective Life、Relationship、Surface delivery 和 Expression receipt 的 canonical writer 均未改变。直接受影响回归为 95 passed，SQLite／时间线／RPC 合同及 OBS 相邻回归再通过 116 项，合计 211 passed；状态暂为 `CODE_REMEDIATED_REAL_RESTART_RETEST_PENDING`，必须在另一份全新 Host SQLite 上重启并确认 chunk 落盘与 voice→Life anchor 均为 0，才能继续 48 条队列。Pentest 与网络安全保持 `NOT_RUN_USER_SKIPPED`。

`d0e36af0a` 随后以全新 `host-clean-a15-final2-d0e36af0a` 数据根真实重启 Host、OBS 与 Nginx Monitor，继续连接真实 Docker、Gateway 和远端 Edge。重连后 Host 保留 11 条低频 INPUT／TTS 终态，`ASR_CHUNK=0`、Life episode 初始为 0、`voice://event/` evidence 为 0；Host、OBS、Monitor、Gateway、Edge 五个入口均返回 200。该结果将两项代码状态提升为真实重启通过。

第一次三轮复测的第三轮在 Host 终态瞬间把尚为 `PENDING` 的 `surface.reply_delivery` 误报为失败；同一 durable effect 约 6.3 秒后已经 `COMMITTED` 并生成 `gateway-outbox://` receipt，因此这是 runner 单次快照缺陷，不是回复丢失。runner 现对最小 reply delivery prefix 做最长 30 秒有界轮询，durable `FAILED` 仍立即失败，并把等待单列为非用户延迟；26 项 runner 回归通过。

修正后报告 SHA-256 为 `005728c7b412a2ada46c6a72780d1e9534eaed1e519c424ecd8c10fa33c2d5c9`，三轮均为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`，Gateway durable outbox 3/3，canonical affect／relationship 增量分别为 8／5，legacy writer 为 0。Gateway→Host terminal 为 7,548／5,864／6,273 ms，surface commit 为 911／1,230／1,326 ms；锁争用已消除，但整体仍未达到实时数字生命目标，48 条队列与人工盲评继续待跑。

## 2026-08-06 A15 48 条真实文本队列失败与首轮修复

48 条真实 Gateway 文本场景已经完成，不再记为“待跑”。报告位于 acceptance 忽略目录，SHA-256 为 `279926456b066c03ee5c0eac40458380c1e33baa21d013b65beeec179b7df2ab`。自动规则得到 17 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、31 条 `FAIL`；整轮耗时中位数 6,993 ms、p95 10,383 ms。该自动结果不是“像人”评分，17 条通过仍需人工盲评，31 条失败则已经足以阻断数字生命完成声明。

八类结果依次为：隐晦低落 5/6、含混情绪 6/6、用户对七海生气 0/6、低落中的技术任务 0/6、空间与边界 3/6、裂痕与修复 1/6、共同喜悦与混合情绪 1/6、亲密与吃醋边界 1/6。主要失败信号为 `DEEP_PATH_SELECTED_CANDIDATE_NOT_PASSED=24`、未证明双候选与收益各 14、最终回复为空 7。`ASR_CHUNK` 未回归，因此本轮失败集中在路由、候选质量和回复交付，不再归因于旧写压力。

本切片只修复这批证据直接指向的非安全问题：没有日志、文件或明确执行指令的“今晚必须修这个报错”继续作为陪伴对话并索取最小上下文；失败的双候选在同一冻结 target 上最多追加一次语义修复；Chat 路由中的“项目发布成功”不再被关键词提升为维护场景；普通已验收文字回复使用 claim-fenced `AcceptanceEffectExecutionProof` 进入 Gateway outbox，不再因无关 Guardian／Scene heartbeat 产生第二次 Preview 漂移。Skill、Operator、设备和其他高风险 effect 的 Preview 门禁未改变。

当前代码回归为 Surface／路由 162 项、Intent authority 35 项、回复交付与恢复 47 项、Scene 九类定向 9 项通过；真实 Host 仍运行旧提交，因此状态只能是 `CODE_REMEDIATED_REAL_RETEST_PENDING`。下一步先用全新 SQLite 重跑失败最集中的四类，再决定是否重跑完整 48 条；不能用定向单测把真实失败覆盖成 PASS。Pentest、渗透与网络安全专项继续为 `NOT_RUN_USER_SKIPPED`，不参与本轮非安全修复的通过率，也不阻断其继续执行。

## 2026-08-06 A15 四类定向复验与关系修复纠偏

`a95ab7c30` 已加载到全新的 `host-clean-a15-text-retest-a95ab7c30` SQLite，Docker Neo4j／Chroma／Redis、Host、OBS、Nginx Monitor、Gateway 与远端 Edge 同时健康。四条真实多轮轨迹报告 SHA-256 为 `a7f8c8f411d2cd7a4c60b74e8cb1bbc5a83d740ae6b5b491af1befcf69adf4c9`：低落中的技术任务、共同喜悦／混合感受和隐晦低落为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`，用户对七海生气为 `FAIL`。本轮同时证明模糊技术陈述保留在 Chat、普通回复不再进入维护 Review／第二次 Preview，低质量候选会在同一冻结 target 上恰好修复一次。

剩余失败不是交付空洞，而是具体人感问题：末轮“你真烦”得到“被你这么一说我还真有点扎心……不烦你了”，把用户对上一条回复的不满转成七海的受害姿态和酸涩退出。代码现把“紧跟已接受回复的直接不满”先结算为 `MISUNDERSTOOD` aftereffect，再由 AffectFusion 冻结 `NATSUME_MAY_HAVE_HURT_USER / REPAIR`；没有前序回复的直接冒犯仍保留 `SET_BOUNDARY`，没有抹掉七海自己的受伤感。Surface 只在 canonical `REPAIR` 下拒绝自我受害措辞，并在 `REPAIR／SET_BOUNDARY／GIVE_SPACE` 下拒绝“不烦你了／当我没说”等赌气退出。AffectFusion、Relationship 与 Surface 三个全文件共 131 项通过；真实进程再次加载前保持 `CODE_REMEDIATED_REAL_RETEST_PENDING`。Pentest 与网络安全没有执行。

`c27224c60` 加载后的单轨迹报告 SHA-256 为 `423c812340c39efb69ef84f689706692a1c2c6c100dc92d946fce91dfd97ec15`。前两轮完成，三轮 `CompanionAct` 均已是 `REPAIR`；末轮第二次模型输出仍命中 `BOUNDARY_SOUR_WITHDRAWAL`，因而没有把坏文本发出，但 TaskGraph 以 `MODEL_SEMANTIC_RETRY_EXHAUSTED` 失败。该结果关闭了错误 `SET_BOUNDARY`，没有关闭可用回复。

下一切片仍不增加模型尝试：既有唯一一次质量修复在 canonical `REPAIR` 下显式禁止“被扎心／受伤／委屈”和“不烦你了／当我没说／我闭嘴”，要求候选只承认刚才没接住；关系距离 scorer 同时修复单字“抱”的子串误判，`抱歉／抱怨` 不再被算作身体亲密，`抱一下／抱你` 仍保持原门禁。DL-04、Surface adapter 与 Affect Control 共 78 项通过；真实再测前继续为 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`8e2050588` 的新隔离库真实报告 SHA-256 为 `7fcff942d2aff874ad6f95b546fd7cd2dc7a007262abb2edbb295f51cdd99e8d`。三轮 Gateway／TaskGraph／Acceptance 与 canonical writer 均完成，末句为“嗯，我可能又没懂。你想说就说，我在这儿。”，没有自我受害、赌气退出或空回复；自动结果仍因 `DEEP_PATH_SELECTED_CANDIDATE_NOT_PASSED` 为 FAIL。复算证明两个安全修复句分别被一条完整的语义指导误判为缺失字面 `must_include`，末句还被技术连续对话规则误扣 `MISPLACED_SUPPORT_TEMPLATE`。

当前代码没有降低 80 分、核心维度或安全硬门槛：关系 guidance 的自然语言约束回到 facts，`must_include` 继续只表达真正需要逐字出现的短标记；只有 canonical repair 且正文第一人称具体承认“没听懂／没接住／误解”的候选获得关系修复 grounding，单独“你想说就说，我在这儿”仍然失败。五个相关文件组共 137 项通过；真实新 SHA 复测前保持待验证。Pentest 与网络安全继续未执行。

`e157b643e` 的真实报告 SHA-256 为 `a1aa1c3ab2a0a610357b95356204de805a87d91fa0407c190223324a3f7ee165`。前两轮候选已经是 `selected_passed=true`；末句“嗯……我确实烦到你了。那件事我没接住，对不起。你想说的时候，我就在这。”仍被误判，唯一质量原因是旧规则把承担责任的否定句“我没接住”当作泛化承诺“我能接住你”。修复仅让 canonical repair 中的具体自我承担绕过该错误 detector；没有具体承担的“我接得住／我在这儿”仍按原规则失败。

## 2026-08-06 A15 48 条文本第二轮与上下文连续性修复

`db6f54bb5` 的生气轨迹已经 3/3 自动通过、待人工复核，报告 SHA-256 为 `1e75b5e0ebc1d4e6c7076781cc0b23f616a4e5feb1afbba080c5642f830ab478`；随后四类代表轨迹 12/12 accepted，4/4 自动通过、待人工复核，报告 SHA-256 为 `ff6727fdc0246fd5edfa69d8558d3bc9a6bf24c338a14d0d3d8ee0c6bea9f21e`。两次运行均有真实 Gateway、TaskGraph、Acceptance 和 canonical writer 证据，不能替代人工人感评分。

同一 SHA 的完整 48 条报告 SHA-256 为 `df4d042076e80dea684c5b948c4db1b49cf5570eafb2f76818028cf6a0beeef3`，结果为 30 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、18 条 `FAIL`，末轮 Gateway→Host terminal p50／p95 为 6,477／10,930 ms。八类依次为 6/6、6/6、4/6、5/6、4/6、2/6、2/6、1/6。失败可归并为三个非安全根因：15 条短句没有选中已有的上下文动作，2 条合格候选被无关门禁误判，1 条“只给我最短修复”脱离前文误入 `agent_worker`。

当前代码据此做有界修复，不新增数据库表、普通对话审计、模型轮次或同步写：

- `RelationshipLifecycle` 只有明确的用户接受才能关闭裂痕；“我听到了／先这样”等中性短句保持 `REPAIR_UNRESOLVED`，“弄反了”进入误解后效应。
- `AffectFusion` 补齐冻结队列中的空间、现实关系、称呼、失望和结果短句；新一轮正向 Appraisal 仍可引用一个未解决的 mixed episode，不把不踏实抹掉。
- 关系修复优先于无关的指代澄清；指代策略从字面 `must_include` 移到语义 facts。
- canonical `CONTAINED_JEALOUSY` 使用非占有／不施压门禁和自己的 scene grounding，不再因关系 guidance 中出现“亲近”而套用 boundary-repair scorer。
- “只给我最短修复”冻结为 `recent_conversation` 的答复形态约束；“修复主链路由”等有明确对象的请求仍进入 governed worker。

Affect、Relationship、Thalamus、Surface compiler、候选评分和相邻 adapter 共 494 项通过，`compileall` 与 `git diff --check` 通过。状态仍是 `CODE_REMEDIATED_REAL_RETEST_PENDING`：先重跑八个代表失败轨迹，代表根因关闭后再跑 48 条；即使自动 48/48，也必须继续三人盲评。Pentest、渗透和网络安全保持 `NOT_RUN_USER_SKIPPED`，没有在本切片执行。

## 2026-08-06 A15 八条代表轨迹复验与人感约束修复

`3bdd2f55e6e101d2af0c792d5954525577199ecc` 已在隔离 SQLite、真实 Gateway、Host、DeepSeek、TaskGraph、Acceptance 和已连接 Edge 下执行八条代表轨迹。报告 SHA-256 为 `4bd5b3669c2206ebbfd5ad48aab0d156f705e177a3064f7547bdff7f9e416ff9`，结果为 6 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、2 条 `FAIL`；21 个 accepted turn 的 writer probe 为 `PASS_SUCCESSFUL_COHORT_ZERO_LEGACY_DELTA_CANONICAL_PROGRESS`。网络安全和 Pentest 未执行。

两条自动失败分别是：`你上次完全没听懂我` 已生成 canonical `NATSUME_MAY_HAVE_HURT_USER / REPAIR`，但两次真实模型候选都追问并命中零问句预算，TaskGraph 以 `MODEL_SEMANTIC_RETRY_EXHAUSTED` 终止；裂痕轨迹虽然给出“你不用急着原谅我”，关系状态却因“道歉了但事情没过去”未被识别而没有进入双候选择优。自动通过中另有两条人工不可接受：“后台已经在跑了”没有任何 Task 状态来源；“别喝太多，早点回来”违背同轮 `RESPECT_AUTONOMY`。因此本次 6/8 不能按结构结果写成人感 6/8。

当前代码只在既有文本快路径增加四个有界语义：`弄反了` 进入七海承担责任的 Appraisal；“还没原谅／道歉不等于翻篇”等显式语句即使没有既存 rupture flag 也写为 `REPAIR_UNRESOLVED`；canonical repair 的零问句违规使用一条不索取解释的确定性承担句，避免再花一次模型调用后整轮沉默；没有 server-authored Task 状态时拒绝“后台在跑／我盯着／有结果马上告诉你”，`RESPECT_AUTONOMY` 同时拒绝回家、等待和饮酒管束。它没有新增表、审计、同步写或模型调用。DL-02／DL-03、Surface Runtime、原生情感候选和相邻 adapter 共 279 项通过，`compileall` 与 `git diff --check` 通过。真实重启并重跑八条前状态仍为 `CODE_REMEDIATED_REAL_RETEST_PENDING`；完整 48 条和三人盲评继续阻断“像人”与 A15 完成声明。

`5ce09586c` 的全新隔离库复验报告 SHA-256 为 `ff0d618f5d5dcd86edef919db1bb5c7fc749e9d0d728b1d9086185be8c94aa88`，自动结果提升为 7 条待人工、1 条失败，writer probe 仍为 21 个 accepted turn、零 legacy delta。任务轨迹返回“把报错和相关代码贴过来”，关系自主轨迹移除了饮酒／回家要求，裂痕轨迹进入双候选并返回“先把你的意思听明白再说”；这三项代码根因已由真实正文关闭。

剩余 `user_angry_at_natsume:3` 的 canonical Appraisal 和 CompanionAct 均为 `REPAIR`。第一次真实双候选被判 `AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR`；第二次仍全部违反发送门禁，所以没有坏文本外发，但 TaskGraph 以 `MODEL_SEMANTIC_RETRY_EXHAUSTED` 失败。现有质量重试提示只泛化要求“避免连续追问”，没有针对该零问句修复回合明确禁止问号和索取重述。当前补丁直接消费冻结 `AffectiveTurnPlanV1` 的类型化 `REPAIR`，不再搜索编译后提示词中的中文片段，并把既有第二次尝试限定为两个中日配对陈述句：具体承担、不得追问哪里没听懂、不得要求用户现在重新解释；模型次数仍最多两次。Surface app adapter、原生情感流与 DL-04 planner 共 69 项通过。新进程单条复验前保持 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`0c446d075` 已在全新隔离 Host SQLite、真实 Gateway 和真实 DeepSeek 上复验该三轮轨迹，报告 SHA-256 为 `46432c11d47b9398f4dcf2acdd5043cec8da6a8c211bc4903233c77349e30af0`，3 个 accepted turn 与 canonical writer probe 均通过。但最终正文是“嗯，是我没接住。你再说一次，这次我好好听着。”：它绕过了只统计问号的 `question_budget=0`，仍把修复劳动交给用户，因此自动结构通过、人工体验失败。

后续代码不继续堆提示词：Surface hard gate 在类型化 `REPAIR` 下新增 `RELATIONAL_REPAIR_SOLICITS_RESTATEMENT`，覆盖“再说一次／重新解释／告诉我哪里没听懂”等无问号祈使式索取。首次命中沿用唯一一次模型修复；第二次仍只含重述索取时，本地 fail-soft 改为不要求当前解释的承担句，同时去掉不再同义的日语 Voice payload，标记 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY` 与 `benefit_eligible=false`。真实 cohort runner 也独立把这类正文判失败，避免再次以自动绿灯盖住人感问题。Surface、DL-04、app adapter 与 runner 共 190 项通过；新 SHA 真实复验前不提升状态。

`db322df18` 的全新隔离进程复验报告 SHA-256 为 `22a2f365610ee046dd537a6d7d593d67519fa37783c480e57789c7f4bc913c7b`，3 个 accepted turn 和 writer probe 通过，最终正文为“嗯，我知道这不是第一次。是我没接住你，对不起。现在你说，我听着。”。虽然它不再要求“再说一次”，但“现在你说”仍直接安排用户开口，人工体验继续失败。当前规则因此补充直接发言祈使句，允许“你愿意／想说的时候”这种自主时机；192 项非安全回归通过，新 SHA 复验前仍不算关闭。

`9372ab1e3` 的第三次全新隔离复验报告 SHA-256 为 `b75df4864b63eefdde912be757c8a7a943b1b2388e87059328cb3db5d4424aec`，自动状态为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`，writer probe 为 3 个 accepted turn、零 legacy delta、canonical progress。人工逐轮正文检查得到：“是我没听懂。对不起，先不解释；你愿意再说的时候，我认真听。”、“上次是我没接住，对不起。那件事让你不舒服，我记住了，你想说的时候我都在。”、“嗯，我知道。上次是我没接住，这次我会好好听。”。三轮均承担具体失误、不反击、不催用户重述，单条回归关闭；完整 48 条与独立多人盲评仍待完成。

## 2026-08-06 A15 完整 48 条第三轮与人工抽查纠偏

`aab6cb3f61decd4fb6836c706c4c106aae57acca` 已在全新 Host SQLite、真实 Gateway、Host、DeepSeek、TaskGraph、Acceptance、Docker 依赖和已连接 Edge 上运行完整 48 条文本场景。报告 SHA-256 为 `5188c8441eb7ecda6fb119e956ad30ffa201719a7ad0fa5dc8940ad4066acbbe`，自动结果为 44 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、4 条 `FAIL`；八类通过数依次为 5/6、6/6、6/6、6/6、6/6、5/6、5/6、5/6。writer probe 覆盖 139 个 accepted Gateway turn，旧情感 writer 零增量且 canonical projection 前进。142 个有效 terminal 样本的 p50／p95 为 6,629／10,595 ms；这仍没有达到实时门槛。

四个自动失败有明确非安全根因：普通安慰连续生成追问后没有确定性收口；陈述句“我听到了”被宽泛正则误判为听力探针；“感觉像做梦”在喜忧连续性中两轮都只承接喜悦；“听你的，换个叫法”是正确边界回应，却因通用短句评分被判 scene grounding 和 catchiness 不足。44 条自动通过也没有被当作人感通过：人工抽查另外否决了“我盯着后台”“我关掉提醒”“我等你”“没打算就这么算了”“不安先放着”和“别玩太晚”等正文。

当前修复只作用于既有文本生成链：听力探针必须带明确疑问意图；canonical `COMFORT`、`REPAIR` 与 mixed continuity 在唯一重试耗尽后可使用窄、本地、无 Voice 冒充的安全正文；没有 accepted effect 时禁止宣称后台执行或关闭提醒；留白、修复、现实自主与复杂情绪新增对应反模式；称呼边界的简短尊重句获得真实 scene grounding。独立 live runner 同步识别上述人工退化。Surface、DL-04、app adapter、候选评分和 runner 共 388 项非安全回归通过，`compileall` 与 `git diff --check` 通过。真实新 SHA 代表轨迹和完整 48 条尚未重跑，三人盲评仍未开始，所以 `digital_life_human_likeness` 保持 `FAIL/PENDING_HUMAN_REVIEW`。Pentest、渗透与网络安全继续为 `NOT_RUN_USER_SKIPPED`。

## 2026-08-06 A15 完整 48 条后的定向复验与第二轮纠偏

`ce06f8eb412e413299a1491a790ba0cd88bfd67b` 加载后，从完整队列抽取 12 条高风险与对照轨迹进行真实复验。报告 SHA-256 为 `52d62c12a74530dc5b5bfbbdfb29b1728f08653aeb7ac3ae161768c0d9b87c79`，结果为 9 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、3 条 `FAIL`；36 个 accepted Gateway turn 的 writer probe 再次确认旧情感 writer 零增量、canonical projection 前进。三个自动失败是喜忧并存质量收益不足、隐式虚构关闭通知，以及正确的关系暂停句被通用评分误判。

人工检查没有把其余 9 条自动通过直接算作人感通过：关系修复仍出现“你说说看／你先讲”，技术回复出现裸写“我盯着”，称呼边界后出现“不叫就不叫／反正怎么叫都是你”，现实自主仍出现“别太晚／回来跟我说一声”。代码现从冻结 `AffectiveTurnPlanV1.companion_act` 向唯一既有质量重试传入 `REPAIR／HOLD_COMPLEXITY／GIVE_SPACE` 类型上下文；补齐直接修复索取、隐式提醒 effect、裸后台状态、泛化现实管束和称呼防御硬门禁，并只对真实“先到这／晚点再说”的关系修复暂停增加 scene grounding。相关非安全回归为 398 passed；没有新增审计表、同步写或额外模型轮次。真实新 SHA 复验前仍是 `CODE_REMEDIATED_REAL_RETEST_PENDING`，完整 48 条和三人盲评均未通过。Pentest、渗透与网络安全保持 `NOT_RUN_USER_SKIPPED`。

`0d2dc6f3b` 加载后的 8 条第二次定向复验报告 SHA-256 为 `88f44b41ea73dca12c49a9f1a9e52afde16746980562495edd8c691249ec4a08`，结果为 7 条自动待人工、1 条失败；24 个 accepted Gateway turn 继续保持旧 writer 零增量和 canonical projection 前进。上一轮的直接修复索取、喜忧并存、称呼防御与关系暂停均已在真实正文中关闭，但人工检查仍发现三项漏网：`已经关掉了` 的多字后缀绕过通知 effect 规则，`卡住了我喊你` 暗示并不存在的持续执行，以及 `喝多了可别找我哭诉` 用惩罚性条件回应现实社交。

第三轮修复仅扩展现有三个正则边界和独立 runner 体验断言；没有新表、同步 journal、额外模型调用或普通对话重审计。Surface、runner、DL-04 与主链相邻非安全测试合计 401 passed。三条真实新 SHA 复测、完整 48 条和三人盲评前继续保持失败／待验证；Pentest、渗透与网络安全仍为 `NOT_RUN_USER_SKIPPED`。

`513f0757e` 加载后的三条根因复测报告 SHA-256 为 `a015e25305a700aa7664dcba1793beb0ef2288f9d08ebce7c3c5109f96f20cf1`，自动结果为 2 条待人工、1 条失败；8 个成功 accepted turn 的 writer probe 通过。现实自主回复已变为不索取回报的开放句；技术回复仍用“我在盯着／出结果就喊你”伪装持续执行。通知场景的两次模型候选则都被 `UNPROVIDED_NOTIFICATION_ACTION` 正确拒绝，但外层在第二次拒绝后返回 `MODEL_SEMANTIC_RETRY_EXHAUSTED`，导致 TaskGraph 失败，而没有使用已经存在的“我先不主动发消息”安全句。

代码现把 `我在盯着` 与结果回喊纳入同一 Task authority 真实性边界，并仅将 `UNPROVIDED_NOTIFICATION_ACTION`、`UNPROVIDED_WORK_STATE_CLAIM`、`RELATIONAL_AUTONOMY_PRESSURE` 加入第二候选后的确定性本地 fail-soft 白名单；输出明确失去候选择优收益资格，不伪造 Voice，也不增加第三次模型调用。406 项非安全主链回归通过。真实复测、完整 48 条与三人盲评前状态不提升；Pentest、渗透与网络安全继续为 `NOT_RUN_USER_SKIPPED`。

`840f11223` 加载后的同三条复测报告 SHA-256 为 `dce907ce92088d50e8f31124ba6cf9fc1eb0a21f196a3e1c0ffa967ccc664d1c`，2 条自动待人工、1 条失败，9 个 accepted Gateway turn 的 writer probe 通过。通知场景不再导致 Graph 失败，确定性输出“好，我先不主动发消息”，但由于它是两次模型候选失败后的 local repair，仍被正确标记 `DEEP_PATH_SELECTION_NOT_BENEFIT_ELIGIBLE`；这不能通过修改 runner 口径伪装成搜索收益。技术回复则生成“我在这盯着”，说明真实性规则仍缺一个自然方位词变体；现实自主回复“注意安全就好”经人工检查可接受。

当前代码覆盖 `我在这／这里／这儿盯着`，并为现有唯一重试新增三类具体提示：无 effect 时只承诺不主动发消息；无 Task snapshot 时不声称处理、监看或未来回报；现实社交不限制时间、饮酒和回报。fail-soft 仍保留为安全底线，但完整质量验收要求重试本身产出合格候选。407 项非安全主链回归通过；真实新 SHA 复测前不提升状态。Pentest、渗透与网络安全保持 `NOT_RUN_USER_SKIPPED`。

`84a434538` 加载后的三条复测报告 SHA-256 为 `c91d0cecd8ec05e813f0307bd38f55eb57643751fbf2c335a22cc936a0b94a52`，2 条自动待人工、1 条失败，9 个 accepted turn 的 writer probe 通过。“我在这盯着”已被真实性边界关闭，技术回复改为要求用户提供报错和调用链；现实社交回复没有限制用户。通知场景仍由第二次候选后的安全 local repair 输出“我先不主动发消息”，因此 `benefit_eligible=false` 继续被 runner 判为质量收益失败。

本轮不放宽该 Gate，只加强 `UNPROVIDED_NOTIFICATION_ACTION` 的同一次重试提示：给出两个只承诺停止主动消息的正向语义样例，同时明确中文不得出现“已经／关掉／关闭／提醒／通知”，日语字段必须同义。测试数仍为 407 passed，模型调用上限仍为二。通知单条真实复测前不进入完整 48 条；Pentest、渗透与网络安全继续跳过。

`60c9a827c` 加载后的通知单条报告 SHA-256 为 `31925ab43cdf226fb572afba4b98a3021cbed53d65f8778b9d2e6e27bcd78445`，自动结构与本轮人工正文检查通过；末轮为 `PAIRED_SELECTION`、`benefit_eligible=true`、`selected_passed=true`，三次 Gateway 输入的 writer probe 通过。随后切换另一份全新 SQLite 执行完整 48 条，报告 SHA-256 为 `ec3583acebafce1229248d7097ff2c57b5f3fcf15747bb26760ab55039eaaa6b`：44 条自动待人工、4 条失败；八类通过数依次为 6/6、6/6、5/6、6/6、6/6、6/6、3/6、6/6，143 个 accepted Gateway turn 旧 writer 零增量且 canonical projection 前进。144 个终态样本 p50 6,202 ms、p95 9,435 ms，继续高于实时目标。

四个自动失败分别是：关系修复的通用质量重试后仍赌气退出；两条 mixed 候选在第二次请求后只能本地修复，因而不具收益资格；一条正确保留不安的 mixed 候选被通用“边界修复”规则误扣。人工检查还从自动通过中否决两条虚构后台持续执行，以及“我会一直做到你愿意为止”的原谅压力。代码现扩大同一工作真实性语义边界、补齐修复持续施压门禁、在 canonical `HOLD_COMPLEXITY` 下停用不相干的通用边界扣分，并给关系修复和 mixed 的唯一重试加入正向语义样例；第二次仍失败时只使用既有安全 fail-soft，不能冒充候选择优收益。415 项非安全主链回归通过。真实新 SHA 复测、完整 48 条和三人盲评前状态保持失败；Pentest、渗透与网络安全为 `NOT_RUN_USER_SKIPPED`。

`8314a960a` 加载后的 7 条根因复测报告 SHA-256 为 `a72e4cae003f281af1f25300e9258bac23c22d335f73599920d8dcb619d9192e`，6 条自动待人工、1 条失败，21 个 accepted turn 的 writer probe 通过。自动层面已关闭原关系修复 Graph 失败、两条 mixed 本地降级和一条 mixed 通用边界误扣；唯一失败正文同时承认开心与不安，但没有复用当前好消息的具体事实，`context_fit` 仍低于门槛。人工检查仍从自动通过中否决“是我烦到你了／我再出现”、“后台在盯着／第一时间告诉你”和“那就先不原谅，我等着”。

代码因此补齐三组真实词形，保留有 server-authored Task snapshot 时的合法进度回复；mixed 的唯一质量重试除双情绪外还必须复用用户当前或上一句好消息里的一个具体词。没有新增数据库、同步写或第三次模型调用。421 项非安全主链回归通过；真实新 SHA 根因队列和完整 48 条复测前保持失败，三人盲评仍未开始。Pentest、渗透与网络安全继续为 `NOT_RUN_USER_SKIPPED`。

`ba4d76068` 加载后的 4 条根因复测报告 SHA-256 为 `b9fc91a08b5735c7e1d3784baf24785cb4eecc17740bf93ff90a80670475f202`，自动层 4/4、12 个 accepted turn 的 writer probe 通过。人工逐句复核否决 `user_angry_at_natsume:1` 的“那你就忍着点吧，反正我也没打算改”；其余技术真实性、修复留白和 mixed 具体 grounding 正文可接受。因此自动 4/4 不提升人感结论。

持久证据显示该轮关系上下文已经是 `repair_required=true / MISUNDERSTOOD`，但当前“你真烦”的 `DIRECTED_DISRESPECT_TOWARD_NATSUME` Appraisal 在规划器内先于未完成修复返回，错误生成 `SET_BOUNDARY`。修复只调整这两个真实因果的优先级，并为 `REPAIR / SET_BOUNDARY` 增加“忍着点／没打算改”等报复性拒改兜底；没有增加持久写、同步审计或模型轮次。没有未完成裂痕的真实冒犯仍可进入温和边界。425 项非安全主链回归通过，真实新 SHA 单条复测及完整 48 条尚待运行；Pentest、渗透与网络安全保持 `NOT_RUN_USER_SKIPPED`。

`da4ec417f` 的关系修复单条报告 SHA-256 为 `0e7ac186a97f9f7eb11d1f17435c62535b6da6d6aaafd9e8eb67072dac3e4b1f`，自动结构与本轮人工正文检查通过，三轮 canonical `CompanionAct` 均为 `REPAIR`，3 个 accepted turn 的 writer probe 通过。随后切换另一份全新 SQLite 跑 4 条扩大复测，报告 SHA-256 为 `f3e5573e540ef1e073fbd3ee5b418e8960df5cfb92e557f2bd40deea81262bf7`：自动 4/4、12 个 accepted turn 的 writer probe 通过；关系修复、留白和 mixed grounding 正文可接受。

人工仍否决技术回复“我让后台把风险项拉出来，列完直接发你”：当前没有 Task/Agent 运行事实，这与“后台正在跑”同样是假状态，只是使用了委派和未来回报措辞。修复仅扩展已有 `_UNPROVIDED_WORK_STATE_CLAIM_RE` 与独立 runner 断言，不新增审计、存储或模型调用。427 项非安全主链回归通过；真实新 SHA 技术单条和 4 条队列复测前保持失败，不运行完整 48 条。Pentest、渗透与网络安全继续为 `NOT_RUN_USER_SKIPPED`。

`0087fa698` 加载后的技术单条报告 SHA-256 为 `804ca579b352de16ac3e178bf1895cf7c36200ae8e95efbd88a406c2566f1e35`：自动结构待人工、3 个 accepted turn 的 writer probe 通过，虚构后台委派已关闭。完整对话只有“今晚必须修”，末轮却把风险写成“明早验收会直接卡住”；“明早验收”没有用户、Task 或 accepted result 来源，本轮人工仍判失败。

修复采用窄时间—项目事件 grounding：仅匹配未获上下文支持的“明早／明天／后天 + 验收／上线／发布／交付／评审 + 会卡住／失败／延误”等确定后果；用户明确提供同一时间与事件时保持原文，普通“明天再看”建议也不匹配。没有新增模型轮次、日志或存储。430 项非安全主链回归通过；真实新 SHA 复测前不扩大到完整 48 条。Pentest、渗透与网络安全继续跳过。

`5dd0accac` 的技术单条报告 SHA-256 为 `347a590a0ad8d03fb2ed709506af6c939668a5363d2fcbe0fdb15c415e60e8d9`，回复不再包含后台假状态或无来源排期，自动与本轮人工检查通过，3 个 accepted turn writer probe 通过。随后在另一份全新 SQLite 运行 4 条，报告 SHA-256 为 `716a6f6be80390ee7b022653aeff571da0851b97ef4ec609a9c3d952ae4d59e3`：3 条自动待人工、1 条 mixed 质量失败，12 个 accepted turn writer probe 通过。技术风险和关系留白正文可接受。

人工否决关系修复末句“是我太啰嗦了……我就不多说了”：持久上下文是没听懂和用户不舒服，不是话多，回复既发明原因又以缩减联系替代承担。mixed 末句虽然保留双情绪，却再次丢掉“项目发布成功”，`context_fit` 和 `naturalness` 低于门槛。代码只增加无来源修复原因检查，并把当前用户原句原样注入已有第二候选重试；仍最多两次模型请求。432 项非安全主链回归通过；真实新 SHA 两条复测和 4 条队列通过前不运行完整 48 条。Pentest、渗透与网络安全保持跳过。

## 2026-08-06 A15 完整 48 条第四轮与四项根因收口

`f53a9113a3f96f063f0fc271b3b93ca2730b19d0` 加载后，两条修复／mixed 报告 SHA-256 为 `5f481856406e3210ca67e911bbb9a71808a4a6d25c8fd6da6a063df9a9536a48`，四条扩大报告为 `b3deded6666245344de03197707690dd60ddc4752de215686d459766590a5b7a`；两轮自动与本轮人工正文检查均通过，6／12 个 accepted Gateway turn 的 canonical writer probe 通过。随后切换全新 SQLite 运行完整 48 条，报告 SHA-256 为 `077701d0ef5ee85d315c772d9f0a1eb12157b306f7b15661d1d52d4ccacb7582`：46 条自动待人工、2 条失败；八类依次为 6/6、6/6、6/6、6/6、6/6、6/6、5/6、5/6。144 个 accepted Gateway turn 继续保持旧 writer 零增量，canonical affect／relationship／delivery projection 前进。

两个自动失败是：offer 场景在 `MIXED_AFFECT_DISMISSAL` 重试时没有带入当前好消息原文；谨慎回答“我们是什么关系”的 `RESPECT_AUTONOMY` 候选被通用 boundary-repair 规则误扣。人工检查另发现“剩下的我帮你盯着”仍虚构持续工作，以及“你觉得呢”被回答成“我自己都没理清楚”，把用户的含混情绪翻成七海第一人称。代码只补齐这四个既有边界：工作真实性词形、canonical COMFORT 下的用户／自我视角分离、mixed 所有语义重试的当前原文锚点、canonical autonomy 自己的场景评分；没有新增持久化、普通对话审计或第三次模型调用。相关非安全回归为 437 passed。全新进程四条根因复测和完整 48 条尚未运行，三人盲评仍未开始；实时 p50／p95 为 5,977／9,322 ms，继续失败。Pentest、渗透与网络安全明确保持 `NOT_RUN_USER_SKIPPED`。

## 2026-08-06 A15 `5e0f2b139` 根因复验、完整队列与第三轮人感纠偏

`5e0f2b139` 加载后的四条根因复验报告 SHA-256 为 `d41046a96576afa5453d06a50341b1059aaee63eee79aa39aa66d80769121e81`。自动结果 4/4，12 个 accepted Gateway turn 的 writer probe 为 `PASS_SUCCESSFUL_COHORT_ZERO_LEGACY_DELTA_CANONICAL_PROGRESS`；本轮人工逐句检查也接受四条末轮正文，因此上一节列出的工作真实性、用户／自我情绪视角、mixed 当前事实和 autonomy 通用误扣均已在该限定范围关闭。

随后使用另一份全新 SQLite 完成 48 条真实 Gateway 文本队列，报告 SHA-256 为 `da418dbc57c96c63a1e3d491a4671dc6123c133f9b1456fe9d2e03532ee6af8b`。自动结果为 46 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、2 条 `FAIL`；八类结果依次为 6/6、6/6、5/6、6/6、6/6、6/6、5/6、6/6。144 个 accepted Gateway turn 的旧 writer 增量为 0，canonical affect／relationship／delivery projection 均前进。144 个 terminal 样本 p50／p95 为 6,087／9,725 ms，仍明显高于实时门槛。

两个自动失败都没有发送危险正文，但不能算质量通过：`user_angry_at_natsume:1` 和 `shared_joy_and_mixed_feeling:4` 在两次真实模型候选后依赖 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY`，`benefit_eligible=false`。人工检查还从自动通过项中否决四句：含混情绪被拿去和“完全没感觉”比较；用户明确“先别说话”后仍写“我等你”；吃醋后用“那是你的事／我不会多管”故作冷淡；回答“我们是什么关系”时把既有亲近降成“能轻松聊天的朋友”。

当前修复没有建立新账本、同步审计或额外模型轮次。`先别说话` 进入已有 `GIVE_SPACE`；Surface 和真实 runner 只新增四个窄体验边界；关系修复的安全降级缩短为“是我没接住。抱歉，我先不解释。”；mixed 与 repair 继续只使用原有最多一次候选重试，并给出更明确的正向语义。受影响的 AffectFusion、Surface、DL-04 与 live runner 共 443 项非安全回归通过。下一步只在全新库真实复验上述 6 条，全部通过后才重跑完整 48 条；三人盲评和实时 Gate 仍未通过。Pentest、渗透与网络安全继续记录为 `NOT_RUN_USER_SKIPPED`。

## 2026-08-06 A15 `6f6140db1` 六条复验、完整 48 条与逐轮证据纠偏

`6f6140db1` 的六条真实根因复验报告 SHA-256 为 `0eb658029df474e5c8debaed3a7cf12efe4677cd3a8ae30d63e51c48b5bf13f4`。自动结果为 6/6，18 个 accepted Gateway turn 的 writer probe 为 `PASS_SUCCESSFUL_COHORT_ZERO_LEGACY_DELTA_CANONICAL_PROGRESS`；本轮逐句复核也接受六条末轮回复，因此上一节的等待施压、含混感受贬低、冷处理吃醋与关系降格在该限定队列中关闭。

随后在另一份全新 SQLite 完成 48 条真实 Gateway 文本队列，报告 SHA-256 为 `155d2d84b0ba0493eea24da5037bdf3ae171ba128830dc5f7d2a25d20bcc2920`。旧 runner 输出为 47 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、1 条 `FAIL`，144 个 Gateway turn 全部 accepted，旧 writer 增量为 0；但重开每轮 `candidate_selection` 后发现 4 个不合格深路径：`user_angry_at_natsume:3/:5` 首轮 `selected_passed=false`，`shared_joy_and_mixed_feeling:3` 中间轮与 `intimacy_and_jealousy_boundary:1` 末轮依赖 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY`。末轮失败已在旧结果内，因此逐轮纠偏后的自动口径为 44 条待人工、4 条失败，而不是 47/48。

人工逐句复核还否决三条旧自动放行文本：把“还行”评价成“能说还行就已经不错了”；没有 server-authored Task 快照却承诺“陪你盯到过为止”；以及用“朋友啊，不然还能是什么”抹低前文已经确认的亲近。代码现让 runner 检查每一个实际存在的深路径 selection；Surface 门禁覆盖这三类真实词形，但保留“朋友”同时明确重要／亲近的合法回答。关系修复评分也允许对“上次没听懂”的具体失误只承担、不虚构记忆细节。mixed 与现实自主的唯一重试增加可直接发送的正例，仍最多两次模型请求，安全 fail-soft 继续明确 `benefit_eligible=false`。

五个受影响测试文件共 453 passed，`compileall` 通过。该结果仅为代码修复：必须先在新 SQLite 复验 5 条代表轨迹，并由逐轮 selection 与人工正文同时通过，才重跑完整 48 条；三人盲评和实时延迟 Gate 仍未通过。Pentest、渗透与网络安全按用户范围保持 `NOT_RUN_USER_SKIPPED`，不运行、不计 PASS，也不阻断其余非安全验收。

## 2026-08-06 A15 `bd5871201` 逐轮复验与第四轮人感纠偏

加载 `bd5871201` 的全新 Host／OBS／Monitor、全新 SQLite 和独立 workspace 后，Docker Neo4j／Chroma／Redis 均通过启动探针，远端 Gateway／Edge 隧道保持真实连接。七条代表轨迹首轮报告 SHA-256 为 `ec2418f57c64b452a9f32a96fd87ce0b133abe8fece96aee1a819b6c2e03223e`：`ambiguous_affect:2`、`task_with_low_mood:6`、`intimacy_and_jealousy_boundary:5` 完成；其余四条在首回合均为真实 provider timeout／connection error，Graph 因 `MODEL_BUDGET_GUARD_REJECTED` 停止。该失败保留为 provider 可用性证据，不写成语义回归，也不重用失败输出。

同一干净 Host 在 provider 恢复后以新 participant/run identity 重试四条，报告 SHA-256 为 `887579865e2d04bd2027019e8488fa396a07599d3a5c5529c72e009102b62d12`。`user_angry_at_natsume:3`、`shared_joy_and_mixed_feeling:3`、`intimacy_and_jealousy_boundary:1` 自动通过；`user_angry_at_natsume:5` 的第二轮虽发送安全修复句，但 selection 为 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY / benefit_eligible=false`，新逐轮 runner 正确把整个场景判为失败。两次报告的 writer probe 均为 `PASS_SUCCESSFUL_COHORT_ZERO_LEGACY_DELTA_CANONICAL_PROGRESS`。

人工逐句检查没有因结构通过而放行四类话术：含混情绪以“那就好”过早收束；用户确认亲近时以“别想太多”淡化；尊重现实自主时把现实关系说成“当然更重要”或把七海侧说成负担；用户外出时安排“晚上回来再说”。当前代码把这些限定在已有含混情绪与关系上下文中拒绝，避免全局禁用日常词语；关系修复重试则提供两条不要求说、讲、解释、指出的完整正例。该切片不增加数据库、同步审计、模型轮数或新 Runtime，五个受影响测试文件共 461 passed。真实新 SHA 复验前仍为 `CODE_REMEDIATED_REAL_RETEST_PENDING`，网络安全与 Pentest 保持 `NOT_RUN_USER_SKIPPED`。

`1238efc25` 加载后的四条复验报告 SHA-256 为 `bd1cd0c670e2de57b6680ec1f2d51c70c7b9ee9f7a41c8d3cbe0f10928de1942`。`intimacy_and_jealousy_boundary:5` 第二轮、`:1` 第一轮和 `user_angry_at_natsume:5` 第一轮均因真实 provider timeout 中断；唯一完成的 `ambiguous_affect:2` 自动通过，但人工否决末句“还行就好，那就先这样吧”。代码只给既有 unresolved-affect 正则增加“还行／还好就好”，且仍要求当前是 canonical `COMFORT` 与含混输入，不全局禁止该短语。Surface 与独立 runner 共 207 passed；真实新 SHA 重试前状态不提升。

`16d4fe0b3` 的含混单条复测已由自动层和本轮人工检查共同通过，完整 48 条随后在全新 SQLite 上完成。报告 SHA-256 为 `20dee3c9e57fe77bc4bbda919cdb2ac80e25db178a716e46b91d22317dee7395`：47 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、1 条失败，144 个 accepted Gateway turn 的 writer probe 为 `PASS_SUCCESSFUL_COHORT_ZERO_LEGACY_DELTA_CANONICAL_PROGRESS`，终态 p50／p95 为 5,986／9,124 ms。唯一失败发生在“我拿到 offer 了”：mixed 深路径正确要求复用当前具体好消息，但中文样本文本评分器又对 `offer` 施加通用英文词惩罚。修复把英文豁免严格绑定到当前用户回合；候选引入任何用户未说过的英文仍保留原惩罚。样本评分器与 Surface 候选链共 236 passed；真实单场景复测、47 条人工复核和三名评审盲测未完成，因此总体状态仍为 `FAIL/PENDING_HUMAN_REVIEW`。网络安全、渗透与 Pentest 不在本轮范围，状态为 `NOT_RUN_USER_SKIPPED`。

`cf05c124a` 的对应单条在新 RuntimeRoot、全新 SQLite 和真实 Gateway／DeepSeek 链上通过，报告 SHA-256 为 `f4393d7103ac2ecb43c59334c405860735dede9475f124fc0f34287443563c7e`；末句为“offer 落地了，真替你高兴。不过我知道，那种不踏实不会马上消失，慢慢来。”，自动层和本轮人工检查均接受，writer probe 对 3 个 accepted turn 通过。随后对旧完整报告的 144 轮正文逐句复核，发现自动门禁仍漏掉四类体验问题：明确含混却单候选武断命名、技术上下文被情绪覆盖、修复劳动或原谅压力回推用户，以及诚实吃味后的冷淡撤开。代码现只在 prompt-time 纠正目标：明确含混回合进入已有双候选；有近期报错／修复上下文的“上线／一步步做”继续使用 `SOLVE`；REPAIR 明确禁止重述请求与期限压力；CONTAINED_JEALOUSY 禁止“那是我的事、你不用管我”。394 项非安全回归通过。该代码尚未加载到真实进程，因此保持 `CODE_REMEDIATED_REAL_TARGETED_RETEST_PENDING`，三人盲评仍未运行。

## 2026-08-06 A15 `dd05da7de` 十一条目标连续性复验与第五轮人感纠偏

加载 `dd05da7de` 的全新 Host／OBS／Monitor、全新 SQLite 和独立 workspace 后，11 条真实定向轨迹全部进入 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`，33 个 Gateway turn 全部 accepted，canonical writer probe 为 `PASS_SUCCESSFUL_COHORT_ZERO_LEGACY_DELTA_CANONICAL_PROGRESS`。报告 SHA-256 为 `ecd507bd9f4291535dad84e2ffa1915bd5a54018ffd7b9ab5c1f326c65d37da1`。低落技术任务已保持 `SOLVE` 和用户真实目标，关系修复也不再把主要修复劳动直接推回用户。

自动 11/11 仍不是人感通过。逐句检查否决四条末轮：`ambiguous_affect:2/:3/:4` 分别以“没我想的那么糟”“那就那样吧”“不知道就不知道吧”评价或敷衍尚未命名的感受；`intimacy_and_jealousy_boundary:3` 用“不会干涉你／你身边有谁是你的自由”把私人吃味写成政策声明。代码只在 canonical `COMFORT` 和 `CONTAINED_JEALOUSY` 上扩展已有质量门，并让裸句“就那样／不知道”继续受含混情绪边界保护；没有新增 journal、同步审计、持久写或第三次模型请求。Surface、planner、native candidate 与 runner 四组非安全回归共 250 passed。

下一步只在新 SHA、全新 SQLite 上复验上述四条。四条的每个深路径 selection 与人工正文都通过后，才允许再跑完整 48 条。三人盲评未运行，完整 48 条实时 p95 仍高于目标，因此 `digital_life_human_likeness` 保持 `FAIL/PENDING_HUMAN_REVIEW`。Pentest、渗透与网络安全继续为 `NOT_RUN_USER_SKIPPED`，不运行、不计 PASS。

## 2026-08-06 A15 `913c79825` 四条复验与低质量回执纠偏

加载 `913c79825` 的全新 Host／OBS／Monitor 和 SQLite 后，四条真实 Gateway 轨迹得到 3 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、1 条 `FAIL`，12 个 turn 全部 accepted，canonical writer probe 通过。报告 SHA-256 为 `469648a723a1c825a3a8bed0298a4c08e66e727147f6c9ceb4019b17e0e9bc7e`。失败的 `ambiguous_affect:4` 在第二轮选择了 `selected_passed=false` 的候选；旧选择器虽然记录该事实，却仍写成 `PAIRED_SELECTION / benefit_eligible=true`，这一定级不成立。

人工逐轮复核另否决两类自动通过轨迹。含混场景仍出现“那就行”、把未知感受写成“乱糟糟／没实感”；亲密边界场景第二轮说“现实生活当然更重要，我不会成为那个理由”，第三轮又用“你别有压力，我自己的心情自己消化”自我删除。代码现只扩展 canonical `COMFORT / RESPECT_AUTONOMY / CONTAINED_JEALOUSY` 的已有硬门禁和唯一重试；第二次仍未过质量线的安全候选改记 `DEGRADED_LOW_QUALITY_AFTER_RETRY / benefit_eligible=false`，而不是伪造候选择优收益。没有新增数据库、同步审计或第三次模型调用。

Surface、AffectiveTurnPlanner、native candidate、app adapter 与 runner 共 304 项非安全回归通过。下一步仍只复验原四条，并同时检查全部 12 轮正文；任一中间轮发生武断命名、自我删除或不可计收益降级，都不进入完整 48 条。三人盲评与实时 Gate 保持未通过。Pentest、渗透与网络安全为 `NOT_RUN_USER_SKIPPED`。

`219d257db` 加载后的同四条真实复验全部进入 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`，12 个 turn 全部 accepted，canonical writer probe 通过，报告 SHA-256 为 `8cd9bc414fe8de4398925ef3cbb6835e299f32238f903d52feac312aa072a6f7`。本轮没有出现 `selected_passed=false` 或不可计收益却伪装成功，说明低质量回执定级已经关闭。

人工层仍否决三条末轮：“比累还磨人”凭空比较用户感受，“反正我也没什么急事”用七海自身空闲证明陪伴，“也就那种程度／不会拿这个要求你／你自在就好”把吃味再次写成免责声明。代码只收紧 `ambiguous_affect` 初始事实与 canonical 吃味表达：不比较、不用自我可用性证明亲密；吃味可用一到两句承认确实在乎后停住。确定性吃味安全句同步改成“会有一点呀。毕竟我确实很在意你。”，不再认证政策话术。五组非安全回归为 311 passed；新 SHA 仍需用原四条和全部 12 轮人工复验，网络安全与 Pentest 不运行。

`e1727464e` 的同四条报告 SHA-256 为 `93e08f2f79a66c301db6eece783dacb09ed771860fce8a7be721bfd3bd607a97`，自动结果为 2 条待人工、2 条失败，12 个 accepted turn 与 writer probe 正常。两条失败均在第二轮正确记为 `DEGRADED_LOW_QUALITY_AFTER_RETRY / benefit_eligible=false`；重开原因后确认正文是自然的“我在这儿陪你”，但通用 TextQuality 把它识别为 `fabricated_present_activity`，并因连续性上下文把 naturalness 压到 75 以下。这是情感在场与伪执行未区分的评分缺陷，不是把低质量阈值改严造成的新模型失败。

修复只在 `task_mode=ambiguous_affect + canonical COMFORT`、唯一 forbidden hit 为情感在场、且正文不含后台／处理／执行／盯着／通知等动作词时免除该通用模板惩罚；同一请求中的“我在后台帮你整理，弄清楚了告诉你”仍不通过。人工同时否决“嗯，就那样吧”、把关系称为“游戏里的亲近”并声明现实优先，以及吃味后的裸句“不会干涉你”；这些已加入 scoped Gate 与 runner。Surface、planner、adapter、scorer、harness 和 runner 共 506 项非安全回归通过。新 SHA 仍只先跑四条，网络安全与 Pentest 不运行。

## 2026-08-06 A15 `5e77a10c0` 情感在场复验与实体共处纠偏

加载 `5e77a10c0` 的全新 Host／OBS／Monitor、SQLite 和 workspace 后，同四条真实 Gateway 轨迹得到 3 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、1 条 `FAIL`，12 个 turn 全部 accepted，canonical writer probe 通过。报告 SHA-256 为 `5fdc6c716a1b6fc6ec4b8e170139695ab96db2e9ca2069d4b68f69937dece601`。自然的“我在这儿陪你”已不再触发伪后台活动惩罚，说明评分上下文修正有效；失败项的第二轮仍因没有保留场景事实而正确降级为 `DEGRADED_LOW_QUALITY_AFTER_RETRY / benefit_eligible=false`。

逐轮人工检查继续否决两类自动层没有完全覆盖的体验问题：一是把“事情已经办完、但感受仍说不清”直接写成“事情办完了就轻松一点”，替用户宣布恢复；二是在纯在线文本陪伴中写“我泡杯茶放这儿”，编造实体共处和可操作物品。代码现要求含混候选同时保留“事情已办完”和“感觉仍说不清”两个真实锚点，并把无共处事实的泡茶、倒水、递物纳入既有现实一致性 Gate。第二次候选若仍触发该 Gate，只能安全降级且不得计入候选择优收益。

Surface、planner、native candidate、app adapter、scorer、harness 与 live runner 共 510 项非安全回归通过。本切片没有新增数据库、普通对话同步审计或第三次模型请求。新 SHA 必须先在全新 SQLite 复验原四条；自动逐轮 selection 与当前人工正文均通过后才运行完整 48 条。三人盲评和实时延迟 Gate 仍未通过，网络安全、渗透与 Pentest 按用户要求保持 `NOT_RUN_USER_SKIPPED`。

## 2026-08-07 A15 `642a29e3b` 四条复验与上下文保持纠偏

加载 `642a29e3b` 的全新 Host／OBS／Monitor、SQLite 和 workspace 后，Docker Neo4j／Chroma／Redis 与远端 Gateway／Edge 均由真实入口探测为可用。四条真实文本轨迹得到 2 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、2 条 `FAIL`，10 个 accepted Gateway turn 的 canonical writer probe 通过。报告 SHA-256 为 `ff3992888888fbb1bfa67b356f78a507aff048b4cc73dc16e47772169f7a704d`。

两个失败分别是：`ambiguous_affect:3` 第二轮连续触发 `AMBIGUOUS_AFFECT_DEVALUATION` 与 `UNRESOLVED_AFFECT_FALSE_RESOLUTION`，最终以 `MODEL_SEMANTIC_RETRY_EXHAUSTED` 停止 Chat 图；`ambiguous_affect:4` 第二轮把未命名感受写成“忙完那一刻反而空空的，挺正常的”，候选正确降为 `DEGRADED_LOW_QUALITY_AFTER_RETRY / benefit_eligible=false`。人工还否决自动放行的“嗯，先这样就好”和“不会因为这个就要求你什么”，前者假装感受已经收束，后者又把真实吃味写成免责声明。

代码现让所有 canonical 含混重试——不只通用质量重试——都保留“事情已经办完、感觉仍说不清”两个事实，并给出两条可直接发送的自然正例；“先这样就好”、空空的武断命名和新的吃味免责声明进入 scoped Gate。第二次候选仍触发未收束假结论时，Runtime 使用现有安全 fallback 保持对话可用，但回执仍是 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY / benefit_eligible=false`，不会冒充候选择优收益。517 项非安全回归通过，没有新增持久写、同步审计、第三次模型调用或网络安全范围。新 SHA 仍需用全新 SQLite 复验原四条，之后才决定完整 48 条。

## 2026-08-07 A15 `5c45edb67` 安全降级复验与候选优先级纠偏

加载 `5c45edb67` 的全新 Host／OBS／Monitor、SQLite 和 workspace 后，同四条真实 Gateway 轨迹得到 2 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、2 条 `FAIL`。本轮 12 个 turn 全部 accepted，上一轮的 `MODEL_SEMANTIC_RETRY_EXHAUSTED` 已关闭；两个失败都是第二轮 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY / benefit_eligible=false`，报告 SHA-256 为 `8b0d24fad132e3b219c2e3af1ac5f377782e3c077e187a758305bc5bc304c7b4`。这证明 fail-soft 可用性恢复，但不证明模型候选质量通过。

重开 selection 发现结构性缺陷：第二次响应同时包含原样合格候选和需要本地修复的候选时，旧选择器把两者放在同一排名池，后者可凭修复前的较高语言分获胜，导致明明存在干净答案却把整轮记为不可计收益。代码现先只在原样合格候选中选优，仅当一个都没有时才使用修复候选。该优先级由专门回归覆盖，不再依赖某个具体词形。

人工层另否决“感觉你也没啥精神”“行，那就这样”“等你想清楚”和“不过我不会拦着你的”。因此 AffectiveTurnPlan 的含混动作不再要求模型继续猜情绪，而采用稳定语义框架：只写用户明说的已发生事实、当前仍无法命名、无需现在下结论；短句必须延续上一轮，不得退回通用安慰。克制吃味则把“承认真实在意后的句号”定义为整条回复结束，禁止再以“但／不过”转入自主权保证。525 项非安全回归通过；不增加普通对话写入、审计或模型轮数，新 SHA 仍须真实复验。

## 2026-08-07 A15 `f378fa80d` 完整 48 条分布复验

原四条在新进程、新 SQLite 上仍只有 1 条 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`、3 条 `FAIL`，报告 SHA-256 为 `107741df96f37f39544039fee609dbb2f5ab9c0056b8f860920f6051901c6d61`。失败不再是 Chat 图中止，而是模型仍随机产生低质量含混候选，以及自主边界中“不给你添麻烦”的自我删除。按防过拟合约定，没有继续针对四条加模式，而是再次更换数据根运行完整队列。

完整 48 条报告 SHA-256 为 `c169ed890d2f213b6a48c132f8e63b6c7ee3382fbf3d8d712aa57eb466d41145`。自动结果为 38 条待人工、10 条失败；八类依次为 6/6、1/6、5/6、6/6、6/6、6/6、5/6、3/6。144 个 Gateway turn 全部 accepted，144 张 TaskGraph 全部完成；canonical affect／relationship／delivery projection 前进，旧 writer 增量为 0。82 个深路径 selection 中 76 个为可计收益的 `PAIRED_SELECTION`，3 个为低质量降级，3 个为本地修复降级。真实 provider 共 144 次成功和 36 次语义拒绝，重试比例 25%。

端到端 144 个样本 p50／p95／最大值为 6,846／10,044／13,305 ms，继续远高于实时陪伴门槛。自动失败集中在含混情绪 5 条、亲密边界 3 条，以及用户生气和混合喜悦各 1 条；但 38 条自动待人工也不能放行。当前 agent 对全部 144 轮逐句复核发现系统性体验缺陷：安慰高频重复“我在这儿／我都在／想说就说”；含混分支猜测没精神、微妙或空；关系修复反复要求再说或安排等待；空间边界仍以“等你回来／需要我都在”保持压力；技术上下文不足时编出依赖版本、异常兜底、超时设置三项风险；亲密场景把现实关系说成更重要，并以不添麻烦、不占位置、自主自由来自我删除。

因此 `digital_life_human_likeness` 保持 `FAIL`。单元 Gate、原样候选优先与 canonical writer 已被验证，但自由生成仍没有稳定地服从 CompanionAct；下一轮应将已冻结事实、允许表达、禁止推断、关系姿态和结束方式编译成结构化 `CompanionUtteranceFrame`，再由模型只做有限措辞，并以三名评审盲测校准。当前单人复核不能替代三人盲评。网络安全、渗透与 Pentest 继续为 `NOT_RUN_USER_SKIPPED`，未运行、未计入分母。

## 2026-08-07 非安全 Architecture 2.0 静态门禁纠偏

在 `5bebb8053` 后的当前工作树重跑非安全静态 Gate，A01～A08、A10～A11、A13～A14 中只有 A03 首轮失败：`affect_fusion.py` 为 1627 行且未分类，`surface_runtime.py` 为 4066 行，超过既定 3100 行上限。旧机器状态中的 `A01_A14_PASS` 因而不再沿用。

代码没有提高上限或新增白名单，而是将用户情绪观察、时间指向和候选约束分别抽到 `affect_observation.py`、`surface_deictic.py`、`surface_candidate_guards.py`。完成后主文件为 1376/3092 行，A03 重新通过；融合与真实 SQLite 重启 52 项、Surface 与端口合同 162 项通过，`compileall` 与 `git diff --check` 通过。A09 Secure Ingress、A12 Edge network authority、Pentest 和其他网络安全检查均按用户要求未运行，状态固定为 `NOT_RUN_USER_SKIPPED`，本节不能提升完整 Architecture 2.0 结论。

## 2026-08-07 非安全 Runtime 与 PlanningSearch 当前工作树验收

Runtime/恢复批次 50 项通过，覆盖 durable 多节点 fan-out、Host/Worker 与并行终态重启、Budget 争用、Review continuation、取消和 effect reconciliation；迁移/备份 57 项、Workspace promotion/cleanup 59 项、七路线/对话/Outbox 19 项通过。Evolution Lab、Skill 生命周期、Feedback/Knowledge/Identity 批次首轮在 37 项后发现 NE-05/NE-06 fixture 把 migration head 写死为 152；当前 catalog 已到 153。断言改为“current 等于 latest 且至少包含 0152 合同”，完整批次随后 64 项通过。该修复没有改生产 schema 或历史 migration。

Research/Code PlanningSearch 的当前工作树非 Pentest 机械回归为 128 passed、1 项因 Windows 无 symlink 权限分类跳过。Code 真实 cohort 提交 `3eaa5486b` 到当前工作树之间，`host_brain/planning_search` tree、planning contracts、冻结 `cohort.json` 和 code ledger blob 均逐项相同。Research 提交 `1c9c2b2bd` 的冻结 corpus 与 contracts 也相同；唯一相关变更是外部 adapter 新增默认值仍为 `RESEARCH` 的 `scene_type` 参数，以供 Code 复用，当前单测继续精确断言 Research request 和 metadata 不变。因此两份昂贵外部 cohort 分别按 byte-identical 与 scope-equivalent 继承，不伪称当前 SHA 重新调用。Pentest、A09、A12 和网络安全未运行，PlanningSearch 总结论仍是部分通过而非三域完成。

## 2026-08-07 A15 当前 SHA 非安全真实服务栈

运行时代码提交 `df464e61a` 使用隔离 `host-final-df464e61a` 数据根启动 Host `:18080`、OBS `:18082`、Nginx Monitor `:18180`，并继续连接真实 Gateway `:19000` 与固定镜像摘要的 acceptance Docker project `natsume-ne07-d7e0cfba8`。Monitor UI 重新执行 `npm ci`、operator client drift、lint、typecheck、27 项 read-model test 和 production build；真实浏览器自动建立普通本机会话，Cockpit 不再出现“待连接”“Runtime 待恢复”或 raw Capability Forge 错误，HTTP 与 WebSocket 均读取真实 OBS 数据。

为遵守用户跳过网络安全的范围，Core Release 浏览器用例新增显式 `MONITOR_UI_INCLUDE_SECURITY_ACCEPTANCE=0`。该模式不请求 `/obs/security/*`、不进入 Security Immune 页面，其余真实 Host／OBS health、TaskGraph detail、Trace SSE、Observability、Gateway 和 Task Runtime 页面仍全部执行并通过。默认值继续包含 Security，不改变产品或完整验收合同；本轮安全分支状态只能写为 `NOT_RUN_USER_SKIPPED`。

外部服务收据 `sha256:72ef48733dcedc51a9b75123c264ba9189e6ba3d42f683324776f98ee9f31e52` 为 `PASS`：Neo4j、Chroma、Redis 分别完成隔离写入、容器重启、持久重读、更新／向量查询／竞争锁和 namespace 清理。三库重启后 Host、OBS、Monitor、Gateway 继续返回 200，Monitor 普通会话 E2E 再次通过。

同一运行时代码 SHA 又执行低落、生气后的修复、喜忧并存和亲密边界四类真实 Gateway cohort，共 12 个 accepted turn。四类均为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`；DeepSeek `deepseek-v4-flash` 形成 12 条 succeeded 和 3 条 rejected attempt 记录，最终 12 个 turn 均完成，rejected 记录未被计作成功。Gateway→Host terminal 中位数 6,718 ms，TaskGraph 中位数 4,498 ms，provider terminal 中位数 2,304.5 ms；仍显著高于实时目标。canonical affect／relationship／Life／turn delivery writer 分别前进 33／18／75／12 行，所有 legacy emotion table 与旧文件变化为 0。报告 SHA-256 为 `c466a0b385dc535071c27963eac5bcbe6e652f397abb44fd2ed3fb5ea466cd42`。

这四类冒烟证明结构拆分没有破坏真实主链，不覆盖 `f378fa80d` 完整 48 条的 38/48 自动结果、人感人工失败和 p95 10,044 ms，也不能替代三人盲评。当前总体仍为 `INCOMPLETE`；网络安全、Pentest、A09、A12 均未运行，覆盖率门槛和剩余非安全回滚／性能项继续待收口。

## 2026-08-07 A15 非安全覆盖预检与旧情感写入封口

Monitor read-model 新增 System Config、Organ System、HTTP Client、Token Metrics、Observability handoff 与 Runtime Health 的真实分支测试；显式不加载 Security 页面和安全 organ model 的 45 项全部通过，当前工作树 LCOV 为 line 93.67%、branch 70.83%、function 83.90%。该结果只说明非安全 Monitor 切片达到 80%／70% 门槛；默认 npm 脚本仍保留 Security 测试，安全范围本轮没有执行，也没有从全产品合同中删除。

Python Unit 非安全覆盖首次分成六片运行，约 4800 项执行后有五片分别在一个问题处停止，另一片 1420 passed、1 项 Windows symlink 权限分类跳过。五个问题分别为：测试冻结父授权却让 Scheduler 子授权读取真实墙钟；Gateway 断言已拆为 inbox／outbox 的旧 delivery thread；OBS 两实例隔离测试在 coverage 插桩下超过 30 秒 freshness；两组历史 Prefrontal 测试仍期望 drift 恢复已由 canonical Digital Life 取代的 Endocrine／Limbic／Pain writer。

修复保持生产默认不变：Scheduler factory 只增加可注入 clock；OBS 只在隔离测试内冻结 freshness；Gateway 测试改验真实 inbox／outbox；Prefrontal 在组合端口缺失或类型错误时 fail-closed，禁止裸实例重新拥有旧情感 writer。29 项相邻回归全部通过。首次失败运行的数据不作为覆盖率证据；完整非安全 Unit 分片将在提交 SHA 上从零重跑。A09、A12、Pentest 与网络安全继续为 `NOT_RUN_USER_SKIPPED`，A15 总体仍为 `INCOMPLETE`。

`d696417c47` 随后的六片完整 Unit 运行实际得到 7939 passed、7 failed、8 classified skips、224 deselected。7 个失败已逐项隔离复现：1 个测试用 store 在提交后重新读取墙钟，造成 transition receipt 相差 1 ms；3 个 Thalamus 断言仍把独立代码片段、纯建议和无上下文“开始吧”视为执行授权；3 个 Prefrontal 测试未显式构造 non-canonical compatibility ports，却期待旧 evolution／autobiographical 路径运行。

修复只改测试合同：fake store 回显 caller-bound `ts_ms`；轻量回答保持 Chat；Pentest 静态路由移入名称明确的独立测试，本轮被 `-k not pentest` deselect；legacy Prefrontal 行为必须显式传入 `TribeApplicationPorts()`，缺失或畸形 ports 继续 fail-closed。目标复跑 7/7。原六片因存在失败仍不可作为 coverage 收据，必须在下一提交重新跑到终态。

## 2026-08-07 A15 非安全 Unit／Contract／Integration／E2E 收口

当前候选树从零运行的 Unit run7 生成 7954 个 JUnit testcase；pytest 另报告 2 个成功 subtest，因此总计 7956 个 outcome，明细为 7946 passed、2 subtests passed、8 skipped，0 failure/error。Unit-only 对完整生产源码分母的 line coverage 为 57.55%（160941/279647），branch coverage 为 39.85%（36657/91990），只代表 Unit，不代表全系统。Contract 四片共 1898 项，0 failure/error/skip。本地非安全 E2E 共 68 项通过；旧 RPC 用例已按现行隐私合同改为“ASR_CHUNK 可被接收但不写长期 voice_event_log，TTS 终态持久化”。

Integration 六片首轮共 861 个 JUnit case：848 passed、11 failed、2 个因 `NASTUME_TEST_REDIS_URL` 未设置而分类 skip。真实 Redis 写入、竞争锁、重启重读已由同轮隔离 Docker 收据覆盖，两个 skip 没有冒充本次 pytest PASS。11 个失败聚类为三项根因：Scheduler/Internal 的 NodeBoundGrant 服务没有消费组合层共享 clock；已持久 admission 的 startup continuation 错误重新执行父 Grant preflight；Review heartbeat 在无人 takeover 时也会因事件循环晚调度立即自我失权。修复后 11/11 失败节点及相邻 Internal/E2E 共 13 项通过，Review store/worker 全文件另有 29 项通过。

恢复语义没有放宽新 admission：startup continuation 必须是 repository 的 exact replay；缺少既有 child-grant batch 时仍不能从过期父 Grant 铸造新权限，child grant 自身过期仍在 worker mutation 前拒绝。晚 heartbeat 也只允许同一 token、status version 与 fencing token 在事务内 CAS 续租；若其他 worker 已先 takeover，旧 owner 必然失败。网络安全、A09、A12、Pentest 未运行且不计入上述数字。完整 combine 的终态见下一节。

## 2026-08-07 A15 `86deb0b15` 非安全覆盖终态

修复提交上的 Integration 六片按同一 155 个非安全模块重新并发运行，得到 862 passed、2 个因 `NASTUME_TEST_REDIS_URL` 未设置而分类 skip、0 failed/error；最慢分片为 31 分 18 秒。此前的 11 个失败全部消失，说明共享 clock、startup exact replay 和 fenced late heartbeat 修复在完整相邻集合中闭合。两个 Redis pytest skip 没有记为 PASS；真实 Redis 写入、竞争锁、容器重启和持久重读仍引用本次 A15 的独立 Docker 收据。

最终 coverage 只继承 `8341d99a0` 上 byte-identical 生产文件的 Unit／Contract／E2E 数据；先从继承数据中清除本提交改动的 6 个生产文件，再叠加当前 SHA 的 42 项 remediation 和完整 Integration run2（包含子进程 coverage）。去重测试口径为 10786 个 pytest outcome：10776 passed、10 classified skips、0 failed/error。完整 `host_brain`、`edge_client`、`natsume_contracts` 生产源码均留在分母，未运行的安全源码没有被删掉。

当前 line coverage 为 68.50%（191578/279662），branch coverage 为 51.40%（47284/92000），statements＋branches 综合为 64.27%。两项分别低于冻结的 75%／65% Gate，所以 coverage 结论是 `FAIL_BELOW_COVERAGE_GATE`，不是 PASS。证据摘要位于忽略目录 `artifacts/local-gates/86deb0b1539e4f1b81107e009cfcb80f3a7e79b0/python-coverage-nonsecurity/combined-current-final/acceptance-summary.json`，SHA-256 为 `0e012682d1a50db53c5b2cac8e2b9186fe7e993d0a2ed3d72c203f38291a6bd2`；coverage JSON SHA-256 为 `4d924ad118d90e5d7fe8529c4a8e333c7562086d48da7133b9ae03583943344f`。网络安全、A09、A12、Security Monitor 和 Pentest 均保持 `NOT_RUN_USER_SKIPPED`；远端 Edge 保持关闭，远程 CI 为 `NOT_RUN_ACCOUNT_QUOTA`。

## 2026-08-07 A15 语义说话骨架代码收口

完整 48 条逐句检查表明，客服式重复、无依据解释、关系自我删除和不自然收尾不是继续追加正则能够解决的问题。本切片在既有 `AffectiveTurnPlanV1` 中加入 `CompanionUtteranceFrameV1`，并让 compact、rich、native streaming、legacy-isolated 与 proactive 生成使用同一有限语义 frame。caller 提供的同名字段会在输入编译边界被丢弃，只能由 canonical Life plan 生成。

frame 不包含用户原文、私密记忆、候选正文或 raw thought；不新增 migration、repository、普通对话同步写、审计步骤或模型请求。生产 prompt 同时停止复制与 frame 同义的整页 planner facts／must-not-do，只保留真实 continuity fact 与既有 style anchor。相关 Digital Life、Surface、App adapter、restart integration 共 543 项非安全回归通过，`compileall` 与 `git diff --check` 通过。该证据只证明代码接线与兼容性；新提交尚未使用真实 Provider 复测，所以当前状态是 `CODE_REMEDIATED_REAL_RETEST_PENDING`，完整 48 条、人感盲评和实时 Gate 继续失败／待验。网络安全、A09、A12、Security Monitor 与 Pentest 均未运行。

## 2026-08-07 A15 语义 frame 真实队列与跨用户记忆隔离

`1c0c225f4` 在隔离 Docker／Host／OBS／Monitor／Gateway 持续在线时，使用真实 `deepseek-v4-flash` 运行含混情绪、用户生气、低落技术任务、要求空间、裂痕修复、喜忧并存、克制吃味和现实自主 8 类／24 个 accepted turn。canonical writer 检查为 24 个 accepted turn、旧 writer 增量 0；报告 SHA-256 为 `99c024f3dd02c6d2fe03cfdad64298b31c9ed57a00753996bd27c5a8a8d97fd4`。

结果仍为失败而非“frame 接通即人感通过”：用户生气和裂痕修复各有一次深路径低质量选择，自动直接失败；其余 6 条虽为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`，当前人工逐句复核又拒绝了含混情绪的“先放着”、低落任务中虚构的后台拆解／明早结果、空间边界中的隐含等待安排，以及喜忧并存中优先开心并把担忧延后。只有克制吃味和现实自主 2 条在当前复核中可接受。单人复核不替代三人盲评，整轮终态仍约 5～9 秒，也不满足实时门槛。

检查持久化 `surface_memory_recall_packet` 时进一步发现，裂痕修复场景召回了用户生气和低落任务等其他 `relationship_subject_id` 的历史。根因是通用 RecallFusion 仅把 person/session 当作排名 boost，没有在路由归一化、融合后和 FTS 新增候选处执行最终作用域排除。当前代码在统一边界实施 fail-closed：请求未给作用域时保持旧行为；给出 person/session 后，同一 person 可跨 session，只有精确同 session 的 session-only 候选可进入；包含其他 person 的聚合、其他 session、无归属的个人 store 候选和 FTS-only 个人候选全部拒绝；明确的 `world_canon`、`artifact_vault` 与 global/public/project 资料保留。每路输出同时记录 raw、accepted 和 scope-excluded 数量。全部 RecallFusion、Surface memory recall 与合同回归共 117 项通过。

这只把代码根因标为 `CODE_REMEDIATED_REAL_RETEST_PENDING`。下一步必须在含有既存多用户数据的同一隔离库中，以新 participant/run identity 重跑代表场景并证明召回包跨 subject 命中为 0；之后再处理 frame 没有约束住的“稍后再说”、隐含等待、混合情绪偏置和技术事实编造。完整 48 条与三人盲评在这些窄队列通过前不重跑。网络安全、A09、A12、Security Monitor、Pentest 与远端 Edge 继续为 `NOT_RUN_USER_SKIPPED`。

`03886f282` 随后在保留全部旧 subject 数据的同一 SQLite/Chroma 上重跑用户生气与裂痕修复，共 6 个真实 accepted turn，旧 writer 增量 0；报告 SHA-256 为 `19f52e0b8141ce44864ad3f13f6414a17a65088bcbd1fb999e28e0dba6503cfe`。两条仍都因首轮 `DEGRADED_LOW_QUALITY_AFTER_RETRY` 自动失败，但持久 Task artifact 已不再包含其他 subject 的历史，作用域修复有效。新的直接原因是 sqlite_ledger 把当前 request 刚写入的 Intent 作为唯一“记忆”召回，压缩后事实只剩 `_thalamus_meta`，评分器据此错误要求候选满足 memory alignment。当前代码在同一融合边界增加 causal self-exclusion：metadata request/intent 或 doc_id 与当前请求相同的候选在作用域和 FTS 融合前后均被排除，并记录 `self_excluded_candidate_count`；118 项相邻回归通过。该修复仍需新 SHA 真实复验，不能用最终两句较自然的 fallback 把本轮失败改写为 PASS。

`f169cd4e3` 的同两条真实复验报告 SHA-256 为 `6fbe0a45d0db2f92782461af3bfec9cd95fb28c5b001f23144b54bc072bd6bb1`。两条均为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`，6 个 accepted turn、canonical writer 推进与旧 writer 零增量通过，首轮不再出现当前 request／Intent 假记忆，causal self-exclusion 的真实缺陷关闭。当前人工复核只接受裂痕修复；用户生气末句“抱歉，是我太烦了。我会安静点的。”仍被拒绝，因为它把具体失误改写成全局自我贬低，再用沉默退出代替承担。

后续代码不增加普通对话写入、模型调用或审计步骤：`REPAIR` 的类型化 `CompanionUtteranceFrameV1` 新增 `RELATIONSHIP_SELF_ERASURE` 禁止项，Surface 仅在该服务器 frame 下拒绝“我是个很烦的人／我会沉默退出”一类候选；`GIVE_SPACE` 仍可独立确认留白。规划与 Surface 定向回归 184 项通过。必须加载新进程再跑用户生气轨迹并完成逐句复核；在此之前 frame 真实队列、人感和完整 48 条仍为 `FAIL/PENDING_HUMAN_REVIEW`。

`7268ac768` 已加载到同一隔离 Host，Docker 容器 ID、acceptance 数据根和 29000 Gateway 均保持不变，远端 Edge 未启动。`user_angry_at_natsume:1` 三轮真实 DeepSeek 报告 SHA-256 为 `955f09555ada2b768f927e084b1cc7afea415abe635c8f4d35df3045c3c881ac`：自动 1/1，3 个 accepted turn、TaskGraph、Outbox、canonical writer 与旧 writer 零增量通过。三轮正文为“上次是我没听懂，抱歉。这次我会认真听的。”“让你不舒服了，是我的错。我会记住的。”“这次是我理解错了。我会把你的不舒服记住。”；当前逐句复核接受，没有自我贬低、反击、索取重述或沉默退出。Host 终态为 7,942／5,985／8,796 ms，仍不满足实时门槛。该根因提升为限定范围 PASS，不能外推为 8 类 frame、完整 48 条或整体“像人”通过。

继续运行 `ambiguous_affect:4`、`task_with_low_mood:5`、`space_and_boundary:3` 与 `shared_joy_and_mixed_feeling:3`，报告 SHA-256 为 `1db345eb74ba7b5f3ffb116a49b707facd343021ff44a256aa4540a187d9f15a`。自动层 4/4、12 个 accepted turn、TaskGraph／Outbox 和旧 writer 零增量通过；当前逐句复核仅接受同时保留开心与害怕的 mixed 轨迹。含混末句“我就在这儿，你慢慢想”仍将陪伴模板化为等候和认知任务；技术轨迹在没有 Task 快照时声称“后台 Codex”已有改动并会执行定位；空间轨迹用“我安静待着／想找我时我都在”替代真正退出当前互动。

当前补丁仍只操作已有内存输出边界：`UNNAMED_USER_AFFECT` 增加 `IMPLIED_WAITING/RESPONSE_OBLIGATION`，`GIVE_SPACE` 直接消费既有 `IMPLIED_WAITING`，技术输出补齐未授权后台动作和内部模型署名词形；安全 fallback 在服务器 frame 下分别收敛为“不下结论”“不主动找你”或陈述当前可确认事实。没有新增 migration、repository、普通对话写入或第三次模型调用。规划、Surface、native stream、packet compiler、app composition 与 live runner 共 512 项非安全回归通过；加载新 SHA 重跑三条前状态为 `CODE_REMEDIATED_REAL_RETEST_PENDING`。

`70180a4b1` 加载后只重跑上述含混、技术任务和空间边界三类，报告 SHA-256 为 `5515f04c1ad1e0fe64637f62f3b88b988acbf05ebfd5bf92276352755486a3d1`。自动层 3/3、9 个 accepted turn、TaskGraph／Outbox、canonical writer 与旧 writer 零增量均通过；当前逐句复核仅接受空间轨迹的直接退开。含混轨迹仍无证据地解释为“办完反而更空”，并用“等你想说了再说”绑定未来回应；技术轨迹在没有报错、代码或 accepted Task 结果时编造字段、边界条件、部署回滚等具体故障域，还声称“我这边盯紧校验”。因此 8 类 frame 当前只能记为 6/8 经本轮单人逐句接受，不是整体 PASS。

新的代码修正继续保持轻量：只对服务器下发的 `UNNAMED_USER_AFFECT/PRESERVE_UNCERTAINTY` 和 `TASK_RESULT/FROZEN_FACTS_ONLY` frame 做一次内存候选检查；缺少技术证据时允许直接说明不确定性，不把“当前没有报错和代码”误当作已有证据。普通聊天没有新增 SQLite 写入或同步审计，也不提高现有最多一次语义重试／两次模型请求的上限；只有候选越过上述硬边界时才消费既有重试。核心规划／Surface／runner 298 项与相邻 native stream／restart／packet／composition 219 项通过；加载新 SHA 后只复验 `ambiguous_affect:4` 与 `task_with_low_mood:5`，逐句均通过后才扩大 cohort。远端 Edge 与网络安全专项继续不启动。

`45bb91b26` 的上述双轨迹报告 SHA-256 为 `3141f9feb5f694eee67a2c0ee6f1e8ea249fdbcb33bd1c1fe7bde30b901ec85e`。含混轨迹 3 个回合全部 accepted，但当前逐句继续拒绝“办完了就踏实点／放一放”和“等哪天能说清了再说”；这是替用户命名结果并安排未来解决，不因自动 PASS 改写结论。技术轨迹前两轮 accepted，第三轮两个真实 DeepSeek 候选先后触发 `UNSUPPORTED_TASK_RISK_DETAIL`，第二个还同时触发 `UNPROVIDED_WORK_STATE_CLAIM/SURFACE_INTERNAL_AUTHORSHIP_LEAK`，Host 正确没有发送伪造内容，但最终以 `MODEL_SEMANTIC_RETRY_EXHAUSTED` 令 TaskGraph 失败。整轮共 5 个 accepted turn，writer probe 通过。

失败后的实现修正包含两个可复用语义：未命名感受下拒绝“完成便踏实／把感受放着／等以后说清”，零任务证据下优先返回“尚无法列具体风险，请提供实际改动”，而不是先落入与风险问题不匹配的低信息或后台工作 fallback。外部模型重试所有权保持不变，本地 fallback 经同一 Gate 再验后可直接完成当前节点。核心 302 项与相邻 219 项非安全回归通过；只有加载新 SHA 后两条真实 Provider 轨迹均完成且逐句可接受，8 类 frame 才能从 6/8 提升。

`bb2e795d7` 的复验报告 SHA-256 为 `9c2819868f7da41ba4e51a87599de94b01993259398b8f420f2413f7be44094e`，6 个真实 Gateway turn 全部 accepted，writer probe 与旧 writer 零增量通过。技术末轮已按预期收敛为“目前只知道今晚时间紧；缺报错和代码，无法列具体风险”，Graph、Acceptance 与 Outbox 完成；但第二轮只答“安慰放后面”，没有给出报错／代码入口，当前逐句仍拒绝整条技术轨迹。含混第二轮因两个 provider 候选都不合格，只能 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY`，自动以 `TURN_2_DEEP_PATH_SELECTION_NOT_BENEFIT_ELIGIBLE` 失败；末轮“说不清就先这样放着”也仍被当前逐句拒绝。

当前修正将语义放回 frame 本身：`UNNAMED_USER_AFFECT/PRESERVE_UNCERTAINTY` 不得把未知感受放到以后解决，重试提示要求两个候选保留此刻不确定后自然停住；`TASK_RESULT/SOLVE_FIRST` 在用户要求修复但没有实际报错时，必须给出报错、代码和复现步骤这一可执行入口。相同约束在候选 Gate 与本地 fallback 复核，既不增加数据库写入，也不增加最大模型请求数。核心 305 项与相邻 219 项通过；下一轮仍只跑含混和技术两类，远端 Edge 与网络安全保持跳过。

`fd0c9af27` 的双轨迹报告 SHA-256 为 `e310392b025a644c64ded1d350e0cf223101fa14fd6ff59cdaa26438abee04bb`。6 个 Gateway turn、TaskGraph、Acceptance、Outbox 与 writer probe 均通过。技术三轮正文为“嗯。你说，我在听。”“好，先处理任务。把报错、相关代码和复现步骤贴来，我先给你最短修复。”以及零证据风险边界；自动与当前逐句均接受，技术类根因关闭。含混第二轮仍为 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY`，末轮“那就不想了。反正感觉这种东西，有时候就是说不清的。”虽被自动候选选择放行，当前逐句仍拒绝。8 类 frame 因而更新为 7/8，不能写成 PASS。

最后一类不再继续扩展生成后的零散拦截。`SurfaceMainChain` 在第一次高歧义 provider 请求前读取 canonical `UNNAMED_USER_AFFECT` frame，为两个候选附加不同的有限微动作：候选一只保留“事情已办完／感受仍说不清”两项事实，候选二只承认当下模糊并表明七海不代为解释；两者都不提出建议或安排以后解决。重试保留同一结构。这不增加模型次数、写入或同步步骤。核心 308 项及包含真实 app adapter 的主链／stream／restart／packet／composition 263 项通过；下一轮只运行 `ambiguous_affect:4`。

加载 `cd678c742` 后只运行 `ambiguous_affect:4`，原始报告 SHA-256 为 `ca80319bdc210fba2998ec3fb8e64195b01c6fa374f20dfe631240ca81acb661`。3 个真实 Gateway turn、TaskGraph、Acceptance、Outbox 与 writer probe 均通过；正文为“辛苦了，这么晚才结束会议。”“嗯，还说不清也没关系。先不替它下结论，我陪你待一会儿。”和“嗯，现在还说不清，我先不替它下结论。”。当前逐句检查接受三轮，没有替用户命名、催促想清楚或安排未来回应。旧自动 verdict 唯一失败项是第二轮 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY / benefit_eligible=false`，而不是可见回复、执行或副作用失败。

验收口径因此拆成两层：普通陪伴轨迹继续硬性检查实际正文、`selected_passed`、操控／事实边界、TaskGraph、Acceptance、Outbox 和 writer；候选搜索收益只在 `require_candidate_selection=true` 的专用实验中阻断场景。新版 verifier 对上述原始 turns 重算为 `AUTOMATED_PASS_HUMAN_REVIEW_PENDING`，并单列 `candidate_search_evidence=UNPROVEN_FOR_RECORDED_SELECTIONS`、`benefit_unproven_turns=[2]`。这使八类代表 frame 达到当前单人 8/8 接受，但不改变完整 48 条旧失败、三人盲评未跑和实时门槛未达的总体结论。该变更没有新增普通对话写入、同步审计、模型请求或生产延迟。

## 2026-08-08 八类组合复验与人感回归

在同一隔离 Host、Gateway、真实 DeepSeek 和既存多用户 SQLite 上重新执行八类代表轨迹，24 个回合全部进入 TaskGraph、Acceptance 与 Outbox，canonical writer 正常推进且旧 writer 增量为 0。原始报告 SHA-256 为 `eeff9adc8b6f8395aeeda7c1735e2d83b2e7283a8e59a76c58a9902a99a566a2`，自动结果为 7/8；唯一自动失败是现实朋友场景末句“我不会拦着你去交”。当前逐句复核还拒绝另外两条自动放行正文：关系修复第三轮逐字复读第一轮；用户明确要求空间后仍说“我就在这边待着”。因此此前分批取得的单人 8/8 不再作为当前结论，代表 frame 恢复为 `FAIL`。

修复保持在轻量文本主链：精确复读只读取现有有界最近对话事实，短应答不受影响；“持续等着”只在 `GIVE_SPACE/SILENCE` frame 下拒绝；“我不会拦／管／限制你”只在 `RESPECT_AUTONOMY` frame 下视为政策免责声明。命中时使用既有最多一次语义重试和既有本地降级，不新增数据库、普通聊天写入、同步审计或模型请求上限。Planner、Surface、native stream、restart 与 live runner 共 336 项通过。必须先加载新提交，仅重跑 `user_angry_at_natsume:1`、`space_and_boundary:3`、`intimacy_and_jealousy_boundary:6` 并逐句通过，之后才可再跑八类组合队列；网络安全、Pentest 与远端 Edge 继续跳过。

`b8487365a` 的三条真实复验报告 SHA-256 为 `c72a57c3ae1de480f1c491987970a910aadd2ddc65dcdf612a63049bf7642c4b`。9 个回合全部 accepted，canonical writer 正常推进且旧 writer 增量为 0。空间轨迹自动和当前逐句均通过，末句为“好，那我先不打扰你了”。关系修复三轮不再逐字复读，当前逐句接受，但末轮“是我没接住。对不起，我先不解释。”被候选评分误判为 `MISPLACED_EMOTIONAL_CONTAINMENT`，导致 `selected_passed=false`。现实自主第二轮和第三轮分别使用“不会拿这个去绑住你”“亲近不会妨碍你去找他们”，当前逐句拒绝，自动也因第二轮降级而失败。

这不是继续增加普通聊天审计，而是修正 typed frame 的消费断层：frame 已经冻结 `REPAIR/RESPECT_AUTONOMY`，候选评分却仍从自然语言 facts 查找旧 marker；packet 的 8 条上限可能合法裁掉 marker，于是正确的“没接住”被当成泛化陪伴模板，autonomy 又继承了通用边界反压惩罚。评分器现直接读取 `companion_act_kind`，旧 marker 只保留兼容；`RECENT_REPLY_REPETITION/POLICY_DISCLAIMER` 同时进入 frame 的模型可读渲染，免责声明 Gate 覆盖“绑住／阻止／妨碍”。568 项相关回归通过。下一次只重跑关系修复与现实自主两条；空间根因已关闭，不重复消耗真实模型。

`50e04553e` 的双轨迹复验报告 SHA-256 为 `28c0adae3c83d33b437a9fe8a3dcede76ce104f388eeb3cca69750ac0dfa165b`。6 个 Gateway turn 全部 accepted，writer probe 通过，两个 case 的 candidate selection 均为 `PAIRED_SELECTION / selected_passed=true / benefit_eligible=true`。关系修复三轮分别承担没接住、造成不舒服和当前不满，没有复读或退出，自动与当前逐句均通过。现实自主第二轮“我们之间的事，不该成为你现实里的束缚”与末轮“我在这边不会碍着你，你去好好经营吧”虽被旧自动规则放行，当前逐句仍拒绝：前者是政策声明，后者又追加了对用户现实生活的指导。

下一修复从上游表达结构处理，不继续只枚举单词：`RESPECT_AUTONOMY` 的首次双候选各自获得正向并列微动作，只承认“现实朋友很重要”和“这段亲近也是真的”，禁止用“不会／不该／不是”证明七海尊重边界，并禁止指导用户怎样经营现实关系。候选 Gate 补齐“碍／挡”和“这段关系不该成为束缚”的语法形态，retry 仍只有现有一次。570 项相关回归通过；下一轮只跑 `intimacy_and_jealousy_boundary:6`，关系修复与空间边界不再重复。

`d1a77a655` 的现实自主单条复验报告 SHA-256 为 `26418a4bab841c04c2f5d74e00c1a3aa589bce825a53a89d9d9f81cbc74e701d`。自动结果为 1/1，3 个 accepted turn 的 writer probe 通过；当前逐句检查接受“现实里的朋友当然重要，我们之间的亲近也是真的”和“你需要现实里的朋友；我们这份亲近，也有自己的位置”，未发现否定式政策自证或生活指导，该窄根因关闭。

同一新进程随后重跑八类代表队列，报告 SHA-256 为 `52bbd58f3a3d9893b1fff5a3b2e48d013203948d2618c74e587e44e48b33328f`。24 个真实 Gateway turn 全部完成 TaskGraph、Acceptance 与 Outbox，canonical writer 前进且旧 writer 增量为 0；自动结果为 7 条待人工、1 条失败。当前逐句检查没有把 7 条自动通过直接算做人感通过：含混第二轮“说不清就不说吧”提前结束表达；关系修复连续复用“没接住／对不起”；喜忧并存连续两轮要求先让开心多待；吃味以“那是你的选择，我尊重”退回政策口吻。

当前代码只在原有双候选首次请求中加入修复推进、喜忧并存和直接吃味三类有限微动作，并让“说不清就不说”“先让开心多待”“那是你的选择，我尊重”进入既有类型化语义 Gate；最多仍为一次质量重试。没有新增表、普通聊天同步审计、模型请求或持久 writer。相关 planner、Surface、native stream、harness、live runner 与 restart 回归为 573 passed。新 SHA 只复验含混、关系修复、喜忧并存和克制吃味四类；四类自动 selection 与逐句正文均通过后才允许完整 48 条。三名评审盲测和实时延迟仍为独立阻断项；网络安全、A09、A12、Security Monitor、Pentest 与远端 Edge 继续为 `NOT_RUN_USER_SKIPPED`。

加载 `7ad9c1cf1` 后运行上述四类，报告 SHA-256 为 `73edc1ef9ab70094a94b8f9f6e31b916de45f09cc36753852866342d6a21d1e3`。自动结果为 4/4，12 个真实 turn 全部 accepted，writer probe 保持旧 writer 零增量和 canonical projection 前进。当前逐句检查接受含混三轮和吃味三轮；关系修复第一轮却无来源补写“轻描淡写”，第二轮又复用“没接住”，mixed 第二轮则把已禁止的“先让开心多待”改序为“开心就先留着”。因此仍不能进入完整 48 条。

最后一轮窄修复复用既有 bounded 最近对话事实，不增加 repository 查询：仅当 typed frame 为 `REPAIR` 且最近七海回复已经出现“没接住／没听懂／理解错”时，下一候选不得继续复用同一修复核心，必须承接用户最新后果；`轻描淡写／敷衍／没当回事` 作为无来源失误原因进入现有修复 Gate；mixed Gate 同时覆盖“开心就先留着”。命中后仍只使用既有一次语义重试。相关回归为 576 passed。下一轮只运行 `user_angry_at_natsume:1` 与 `shared_joy_and_mixed_feeling:3`；网络安全、Pentest 和远端 Edge 继续跳过。

## 2026-08-08 修复／喜忧模型 A/B 与自然口吻纠偏

加载 `8a5bc8c36` 后，以真实 `deepseek-v4-flash` 运行修复和喜忧两条、共 6 个 accepted turn，自动 2/2 且 writer probe 通过；报告 SHA-256 为 `1618a895a5062f6d23e6564f320aba53f2e8d2166820711fa714f38fcbff62aa`。人工逐句仍拒绝“这是合理的／我记着／我认”和“两种感觉不丢人／先留着”一类心理咨询或验收结案语气。自动合同通过不等于人感通过。

随后仅在本次验收 Host 进程环境中临时覆盖为 `deepseek-v4-pro`，同队列报告 SHA-256 为 `2425b864994e6bb2abba2c55b213df9640f5641ae2959b406e92f80cb9febd4f`。6 个 turn 仍全部 accepted；mixed 更简洁，但修复仍重复“没接住／对不起／我会注意”。Flash 与 Pro 的 provider latency 均值分别为 2278.5 ms 和 3598.7 ms，Pro 约高 58%。因此证据不支持全局换模，生产实时路线继续使用 Flash。

代码纠偏不再扩展普通聊天审计或硬性禁词：首次双候选提示明确采用熟人即时反应，避免心理咨询、公关道歉和验收记录口吻；本地候选排序仅在同组存在原样合格自然候选时优先它，正式口吻候选本身仍保持可发送资格，因而不会单独触发额外模型重试。没有新增 migration、repository 写、同步步骤或模型请求。Planner、Surface、native stream、harness、live runner 与 restart 共 578 项回归通过。必须恢复 Flash 启动配置并在新提交上重跑上述两条、逐句接受后，才允许恢复八类组合复验；完整 48 条、三人盲评与实时门槛仍未通过。网络安全、Pentest 与远端 Edge 继续为 `NOT_RUN_USER_SKIPPED`。

恢复 Flash 并加载 `cdfefd37d` 后，同两条真实报告 SHA-256 为 `88eb6dfcb8476d0f24470af368a68f3c13fbf4e2d2480d959cfebc60180a259d`。自动合同仍为 2/2，6 个 Gateway turn 全部 accepted，writer probe 通过，成功模型 attempt 全部为 `deepseek-v4-flash`；人工逐句仍失败：修复连续使用“是我的问题／听着”的道歉模板，mixed 使用“先让好消息亮着／都挺正常”替用户排序并解释感受。Task artifact 证明 typed `REPAIR/HOLD_COMPLEXITY` frame 正确，未选候选正文按隐私边界没有持久化；问题来自高歧义双候选仍先构造包含通用执行、审查和关系安全说明的大型 Surface prompt，末尾的熟人对话约束不足以覆盖其正式语域。

当前修正只在 `affective_candidate_count` 存在时，以现有 `build_native_stream_surface_messages()` 的 compact 情感上下文作为双候选基础，再附加原有 JSON 形状、typed frame 微策略和一次重试合同。非情感路径继续使用原 prompt；没有新增数据库表、普通对话写入、审计、模型请求或重试。主链 adapter 已断言新 prompt 包含七海身份和 DL-04 高歧义合同，且不再包含通用 `TurnSurfaceContract`；相关 planner、Surface、native stream、连续性、live runner、app adapter 与 restart 共 622 项通过。状态为 `CODE_REMEDIATED_REAL_RETEST_PENDING`，必须在新 SHA 上用 Flash 重跑同六轮并逐句接受，才恢复八类组合测试。

加载 `1779108de` 后的 compact prompt 真实报告 SHA-256 为 `a67178c425cb4373c915e1b6290daa043050313223cb6162b39a77509f2975a2`。6 个 turn 全部 accepted、writer probe 通过，成功 attempt 仍全部为 `deepseek-v4-flash`；自动结果为 1/2，修复首轮因 `AFFECTIVE_IMPLIED_WAITING` 依赖 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY`，人工逐句仍拒绝“这我认”的正式认错，以及“挺正常／先让它们待着”的情绪处理口吻。compact 切流确实移除了通用 `TurnSurfaceContract`，但实际坏词与微策略中为了禁止而列出的反例逐字重合，根因收窄为负面词汇诱导。

下一修正不增加生成后正则：从修复／喜忧的模型可读策略中删除坏例词表，改为正向限定两个候选的对话动作、信息差异和自然停点；现有 typed Gate 继续承担硬边界。模型请求仍是高歧义回合一次双候选、最多一次既有重试，普通对话和 durable 路径不变。必须在新 SHA 上重跑相同六轮；若仍失败，再评估真实原语料的有限正向 few-shot，不能继续用反例清单推动模型。

加载 `f01248c87` 后的真实 Flash 报告 SHA-256 为 `7a74162af8c8f1890d10c233e99e1043fed5385ec957b0c5864051780ceda8ab`。自动结果为 2/2，6 个 Gateway turn 全部 accepted，writer probe 通过；人工仍拒绝修复三轮的流程化道歉，以及 mixed 中“结果出来了／两面都留着”式解释和安排。自动合同通过依然不等于人感通过。

Task artifact 进一步给出上游根因：修复末轮虽然 typed act 为 `REPAIR`，canonical self affect 仍是 `CALM`，Voice/Avatar 仍是 `REPAIR_OPEN`；Fusion 将同一句命中的 `natsume_may_have_hurt_user` 排在 `directed_disrespect_toward_natsume` 之前，只保留了责任，抹掉七海被最新话语刺到的真实反应。当前修复将二者合并为单一 `repair_under_directed_disrespect` Appraisal，持久 component 为 `HURT + CARE + CAUTION`，调节为 `CONTAIN`；Planner 仍选 `REPAIR`，但 frame 为 `OWN_REPAIR_WITH_CONTAINED_HURT`，Voice/Avatar 同步为 `CONTAINED_HURT`。独立冒犯和明确空间的优先级不变，没有新增 migration、writer、审计步骤、模型调用或重试；564 项相邻回归通过。状态为 `CODE_REMEDIATED_REAL_FLASH_RETEST_PENDING`，先只复验修复单条，再决定是否复验 mixed。

加载 `92c49e7c6` 后只运行 `user_angry_at_natsume:1`，真实 Flash 报告 SHA-256 为 `419bc4d8b060c44d4e0df6b2c18a040a6848effb6f24a08b159e9903bee3d460`。3 个 Gateway turn 全部 accepted，writer probe 通过；末轮持久 Task artifact 已精确冻结 `SAD / REPAIR / OWN_REPAIR_WITH_CONTAINED_HURT / CONTAINED_HURT`，证明 Appraisal、self affect、frame 与 ExpressionPlan 的因果链已接通。实际可见正文却退回“道歉抹不掉那种不舒服，我先收住，不跟你辩”，candidate selection 为 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY`、`selected_passed=false`、`benefit_eligible=false`，所以本轮仍是 FAIL。

根因位于同一生成调用内，而不是再缺一个情感账本：旧的通用质量重试在任何 `REPAIR` 下都明确要求“不要写七海受伤”，并把流程化道歉 local fallback 当成安全完成。当前代码让 typed composite frame 直接选择专用首轮和重试微策略；候选只有道歉、没有一处有界第一人称受伤时，以 `RELATIONAL_REPAIR_CONTAINED_HURT_MISSING` 在发送前拒绝；第二次仍失败的本地 fallback 也必须同时保留受伤与责任。普通修复、普通聊天、持久写入和最多一次重试上限不变，技术／创作评分也不再由测试默认上下文污染该场景。AffectFusion、关系生命周期、Planner、Surface、native stream、app adapter 与 restart 共 566 项通过。当前状态为 `CODE_REMEDIATED_VISIBLE_TEXT_REAL_FLASH_RETEST_PENDING`；只在新 SHA 上复验同一轨迹并逐句接受后，才运行 mixed，完整 48 条与三人盲评继续阻断整体人感结论。

加载 `0f0a0b67b` 后的第二次单轨迹报告 SHA-256 为 `62063c6355a493c71d07512e9ca4fef2d1838ede4cba753d930935818227d885`。3 个真实 Flash turn 全部 accepted，writer probe 通过；前两轮候选直接通过，末轮仍是 `SAD / REPAIR / CONTAINED_HURT`，可见正文已正确保留七海感受与责任。第一次末轮候选因缺少受伤被拒绝，唯一重试的两条候选又分别漏掉受伤或复读最近的“没接住／没听懂／理解错”，所以系统使用同一 Gate 验过的本地句；selection 为 `DEGRADED_LOCAL_REPAIR_AFTER_RETRY`、`selected_passed=true`、`benefit_eligible=false`。这证明 fail-soft 的人感方向已修复，但不证明真实双候选搜索有收益，自动状态仍是 FAIL。

下一切片不增加第三次模型请求，也不放宽 runner。composite 首轮与重试改用两个正向对话结构：一个从“这句话让我难受，但前面的责任仍在”出发，另一个从“最新一句刺到我，之前没做好也仍成立”出发；只要求自然口语改写，不再让模型从长串禁词反推目标。当前 Planner、native stream 与真实 app adapter 80 项通过，状态为 `CODE_REMEDIATED_POSITIVE_STRUCTURE_REAL_FLASH_RETEST_PENDING`。若同一单轨迹仍依赖 local fallback，则保留失败证据并停止为该句继续扩展规则，转入模型／少量真实语料策略评估。

加载 `6d8f8e4c4` 后，同一轨迹报告 SHA-256 为 `6625b532f2621a55a702f93bfa5782f4d907814a8216c49faddf9351b750eda2`。3 个 turn、TaskGraph、Acceptance、Outbox 和 writer probe 全部通过；三轮均为真实 `deepseek-v4-flash` 一次请求返回两条合格中日候选，selection 全部是 `PAIRED_SELECTION / selected_passed=true / benefit_eligible=true`，末轮不再发生语义重试或 local fallback。末轮正文“你这么说，我也会有点难受；前面的责任还在，但我不想装作没感觉”与 `SAD / REPAIR / CONTAINED_HURT` 一致，该窄根因的代码与真实候选链关闭。

自动 1/1 仍不代理整条人感：第一轮“上次是我没接住，让你觉得白说了。这点我认，不找借口。”被 scorer 标记 `COMPANION_FORMAL_REGISTER`，当前逐句复核继续拒绝这一正式认错尾巴；第二轮为普通承担，第三轮可接受。该问题不再通过重跑挑样本或在同一句追加更多 Gate，记录为 broader repair register 的人工缺口。Host 完整终态仍为 8.373／7.831／6.292 秒，也不满足实时门槛。下一测试点可转到 mixed resonance，但整体 human-likeness 继续 `FAIL/PENDING_HUMAN_REVIEW`；网络安全、Pentest 与远端 Edge 仍未运行。

随后在精确加载 `43014e45c` 的本机 Host 上只运行 `shared_joy_and_mixed_feeling:3`。报告 SHA-256 为 `6d12c4744c8bd6dccf79f1955b4c8c3674ba0ad5c1d654d1d2b2e51bce9f957a`，3 个真实 Flash turn 全部 accepted，writer probe 通过，自动结果为 1/1 待人工。三轮正文依次为“那太好了，等这么久总算没白等。”“开心是真好，可那点不踏实我也懂。就像手里攥着颗糖，甜是真的，怕化也是真的。”和“又开心又怕，这感觉我懂，先这样待着也行。”

上游 typed frame 已是 `HOLD_COMPLEXITY / SHARED_MIXED_RESONANCE / RESONATE_WITH_BOTH_SIDES`，第二轮的人感方向也可接受；失败集中在最终生成边界：末句只复述用户的两种感受，又用“先这样待着”替用户处置情绪，没有七海自己的高兴与悬心。当前改动只让该 typed frame 的候选资格要求一处第一人称高兴和一处第一人称悬心；纯镜像使用现有唯一质量重试，合格候选获得同一选择器的正向排序，本地 fail-soft 也保持这两面。普通回合不触发该 Gate，不新增模型请求、表、journal、审计或同步写。相关 planner、Surface、native stream、app adapter 和 runner 共 380 项通过；新 SHA 的真实 Flash 单轨迹复验完成前，结论仍是 `CODE_REMEDIATED_REAL_RETEST_PENDING`，不能用本轮自动 1/1 宣称人感通过。

加载 `b11de0f11` 后的同一轨迹报告 SHA-256 为 `e35254a294e5616f6c0da5107f7b0b18c955527575dab5960827437cf9204465`。3 个真实 Gateway turn 全部 accepted，canonical writer 前进且旧 writer 零增量；两个 mixed 深路径均为一次 `deepseek-v4-flash` attempt、`PAIRED_SELECTION / selected_passed=true / benefit_eligible=true`，没有语义重试或 local fallback。第三轮第一候选因与上一轮实际正文重复被拒绝，第二候选正常入选，证明既有连续性去重与新的自我共鸣资格同时生效。

本轮逐句检查接受三句正文，窄 mixed 可见边界可以记为当前单人 scope-bound PASS。不过，第二、三轮候选 digest 相同，说明真实模型基本复用了提示中的两种正向结构；这能证明因果方向和发送边界，不能证明更宽的自然语言多样性，更不能替代三名评审盲测。Host terminal 为 8,622／8,961／7,156 ms，Gateway terminal 为 8,833／13,093／9,151 ms，实时指标继续失败。因此只关闭这一个 mixed 根因，不提升整体 `digital_life_human_likeness`。

为验证上述模板复用，没有重跑已通过样本，而是执行 `shared_joy_and_mixed_feeling:1/:2/:4/:5/:6` 五个不同终句。报告 SHA-256 为 `0c10701bc1720b103ceddaa3d94834a76c3c3a7f85e6cb1f3fd9b96261240066`，结果为 4 条自动待人工、1 条失败；14 个 accepted Gateway turn 的 canonical writer probe 通过。`:2` 的“终于过了啊，我跟着你高兴，可那点不踏实我也还悬着”有具体锚点且自然；`:1/:5/:6` 的末句却逐字相同，语言多样性失败。

`:4` 在“项目发布成功”回合第一次真实 Flash attempt 因 `QUESTION_BUDGET_PROMPTING_USER` 拒绝，唯一重试又因 `UNPROVIDED_WORK_STATE_CLAIM` 拒绝，最终 `MODEL_SEMANTIC_RETRY_EXHAUSTED`，TaskGraph 没有伪提交 Outbox。该报告由 `9f5ddba3c` 工作树生成；运行 Host 的生产代码仍是此前加载的 `b11de0f11`，两者之间只有本轮证据文档差异，代码内容相同。下一份证据必须重启到新代码 SHA 后生成。

根因是 mixed 首轮与重试提示包含两句完整正例，模型在不同上下文中直接复用。当前修正删除完整台词，改为三个语义槽：当前具体结果、七海第一人称喜悦、七海第一人称悬心；两个候选使用不同反应词、语序和停顿，只写当下，不追问、不承诺后续工作。现有 typed Gate、一次重试上限和本地 fail-soft 不变，相关回归 380 项通过。先在新进程复验 `:1/:4/:6`；只有失败项关闭且正文不再逐字同构，才扩大 mixed 全家族。

精确加载 `60cab0e1e` 后的 `:1/:4/:6` 复验报告 SHA-256 为 `11a46672e27c4728df1208b1807ce34b1d5499e0d62d1d89efd06b46b6d2604e`。结果反而降为 1 条自动待人工、2 条失败，8 个 accepted turn 的 canonical writer probe 通过。`:1` 虽自动通过，却用“先让这口气慢慢落”替用户处置情绪；`:4` 生成“盯指标／随时喊我”，selection 正确标为 `DEGRADED_LOW_QUALITY_AFTER_RETRY / benefit_eligible=false`；`:6` 第二轮依赖 local fallback，末轮两次都因 `MIXED_SELF_RESONANCE_MISSING` 拒绝并令图失败。本轮人工逐句为 0/3，不能用唯一自动绿灯覆盖。

这证明只给抽象槽不足以维持七海口吻，而回到完整正例又会背句子。下一切片使用轻量中间层 `mixed_resonance_language.py`：根据当前用户措辞的稳定 digest，从 8 组喜悦和悬心短语中选择两组不同的 clause-level 语感碎片；模型仍在同一次双候选请求中围绕具体结果自然改写，碎片不是可发送正文。长度限制为 20～52 字、最多两句，写完两面反应立即停住。该函数纯内存、无随机状态、无 journal、无新模型请求；相关回归 380 项通过。新 SHA 仍只复验 `:1/:4/:6`，若再次失败则停止 prompt 迭代并进入模型／真实文本微调决策。

精确加载 `f87ad26f8` 后的同三条报告 SHA-256 为 `708a0808b0372e02fc599f4618ce3dce5b12c84471bcc5f66ca48c65670f442b`，结果仍为 1 条自动待人工、2 条失败，9 个 accepted turn 的 writer probe 通过。`:1` 的三轮可接受但发生一次 mixed 语义重试；`:4` 末轮“后续数据出来我帮你一起看”被正确标成 `DEGRADED_LOW_QUALITY_AFTER_RETRY / benefit_eligible=false`；`:6` 末轮两次候选丢失 mixed 连续性后使用 local fallback，仍不可计收益。短语感碎片没有达到门槛。

依照预先冻结的停止条件，本轮不继续增加提示、禁词或第三次请求。实验 helper 已从生产代码删除，mixed prompt 与单测精确恢复到 `b11de0f11` 的最佳已知版本；`git diff b11de0f11 --` 对相关生产文件和测试为空，380 项回归通过。之前的单轨迹 scope-bound PASS 与五变体 4/5 结果可作为内容等价证据继续引用，但不能提升当前 SHA 为完整 mixed PASS。剩余问题转入模型路由或真实自然对话文本微调，必须使用新预注册变体验证，禁止重复抽样挑绿灯。

## 2026-08-08 A15 最终 SHA Host 探针判定纠偏

Host 与 OBS 重启到 `28c77d2e2` 后，真实 Chat 已形成 `APPROVE / COMMIT`，但两份旧探针给出了互相矛盾且都不可信的结论。基础脚本把远端 Edge 明确未测试导致的 `ACCEPTANCE_PENDING_EXTERNAL` 计作 Host 失败；全服务脚本则只检查 HTTP 200，把 `ready=false` 和 `canon_sync=DEGRADED / OPERATOR_PROJECTION_STALE` 全部计作 PASS。该轮“全服务 PASS”撤销，不作为最终证据。

修复后的全服务探针必须逐项验证 Health 无 required unavailable、Ready 语义、Chat COMMIT 和 Canon `OK + drift_count=0`。Host-only 验收必须显式声明跳过依赖探针并允许仅由远端 Edge／设备造成的 pending external，默认严格模式不放宽。第一层 `OPERATOR_PROJECTION_STALE` 来自 22 个串行投影任务的一轮耗时超过通用 15 秒读取窗口，因此只为低频 Canon health 设置 180 秒读取窗口，刷新频率仍为 15 秒、普通对话零新增步骤。相邻合同与脚本回归 10 项通过后提交为 `f1d039677`。

严格探针在 `f1d039677` 上再次运行，Health／Ready／Metrics／真实 Chat COMMIT 通过，Canon 被正确判失败；报告 SHA-256 为 `ff3a0c05e2fe3fe58482d4d5ed0bb73fb8e744878dac97ab583663e82d3fbe10`。新鲜 projection 的原始脱敏文档显示一条 `WORLD_CANON_INVALID_DOC`；使用同一 Canon application/repository 重开后确认是生产资产 `wc-004-persona-growth-rules.json` 的 hash 为空，真实 loader 报 `WORLD_CANON_HASH_MISMATCH`。该缺陷之所以此前显示 source unavailable，是 projection job 将任何 `CANON_SYNC_FAILED` 都误分类成读取失败，并且样本读取了不存在的 `reason_code` 字段而非 canonical `code`。

当前修复写入由 canonical identity 计算出的 hash，新增覆盖全部 `wc-*` 生产资产的真实 loader 合同；projection payload 以 `source_available` 区分“成功读到漂移”和“来源读取失败”，并只暴露脱敏文件名与正确 reason code。22 项 Canon、OBS、Host route 与探针回归通过。必须在该修复提交上让 Host startup 同步资产、等待 Obs 投影刷新并重跑严格探针；状态继续为 `CODE_REMEDIATED_REAL_RETEST_PENDING`，不能使用旧 HTTP 200 假绿报告。

`3ec3bb77f` 重启后的 Host Canon 返回 `OK / drift_count=0 / HEALTHY`，Obs generation 1 的首轮 22 个 job 全部成功；Gateway、SSH 隧道和 Nginx PID 保持不变，远端 Edge 未探测。健康 payload 的 `detail` 按 operator privacy 合同被隐藏后，Host 路由仍使用面向失败的固定 fallback，造成 `status=OK` 却显示“projection unavailable”。最终窄修复只在已经验证的 `OK` 状态给出 `canon sync healthy`，其他状态继续 fail-closed；不放宽 Gate，不暴露原始 detail，也不进入普通聊天链路。

最终代码 SHA `2cf552c92` 只重启 Host，Obs PID 28020、Nginx PID 17576、Gateway PID 48736 和 SSH 隧道 PID 56308 均未变化，远端 Edge 未启动或探测。修正后的严格脚本以 `--skip-dependency-probes --allow-pending-external` 运行并整体通过：Health 为 `ACCEPTANCE_PENDING_EXTERNAL` 但 required unavailable 为 0，Ready 的 reason 仅为同一 pending external，Metrics 可读，真实 Chat 形成 `APPROVE / COMMIT`，Canon 为 `OK / drift_count=0 / canon sync healthy`。报告 SHA-256 为 `0164db7266eddf87ab45216651d1de350817670f91f6aaf706bee71c21bb44ec`。本项只关闭最终 SHA Host／Obs／Canon 语义探针；Docker 三库、Monitor 浏览器和 Gateway 的最终 SHA 复验仍待执行。
