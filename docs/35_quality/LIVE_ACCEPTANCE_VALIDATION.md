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
