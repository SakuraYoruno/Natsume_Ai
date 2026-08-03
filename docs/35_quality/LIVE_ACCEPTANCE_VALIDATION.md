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

这些事实证明本轮 Gateway、canonical runtime、具身回执和健康投影 P0 闭环可用，不代表完整数字生命发布通过。VTS 情绪热键、barge-in、48 条多轮盲测和完整实时性能门槛仍待完成，整体继续为 `INCOMPLETE`／局部 `ACCEPTANCE_PENDING_EXTERNAL`。
