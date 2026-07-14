受众：CODEX / MASTER
标题：TASK-0494 Bridge loopback、HMAC 与远程暴露审计验证记录

# TASK-0494 Bridge loopback、HMAC 与远程暴露审计验证记录

状态：`IMPLEMENTED_AWAITING_PROTECTED_HANDOFF`。实现 commit：`0932db5d46560ebee0b32c4b33bc4f1606bbc538`。

## 已完成的本地实现

- `NATSUME_BRIDGE_EXPOSURE_MODE` 冻结为 `DISABLED|LOOPBACK|REMOTE_APPROVED`，默认 `DISABLED`。Mate、WeChat、Desktop Pet 还分别要求自己的 kill switch；模板中的三个 switch 均为 `false`，Mate HTTP/UDP 与所有 downstream 默认 loopback，legacy UDP 默认关闭。
- Mate HTTP/UDP ingress、Python→C# plugin、Desktop Pet、WeChat command queue 与 WeChat→Gateway callback 均使用服务专属 credential。动作请求统一使用 10 字段 canonical HMAC；Ingress/plugin key id 与 secret、WeChat command/callback secret 必须彼此分离。
- LOOPBACK 只允许最小匿名 health；任何 action 都先完成 HMAC、principal/scope、TTL、body digest、replay、credential-global rate 和 durable audit。REMOTE_APPROVED 还要求显式 host、approval、read/action 或 source/action allowlist、rate/burst/TTL 和审计路径。
- Mate Python replay 使用独立 SQLite，不再从会轮转的 audit 恢复 authority；未来时间戳 claim 的失效点为 `signed timestamp + TTL`。C# replay 有 100,000 live cap，每 4,096 claim 压缩、每 4,096 claim 或 60 秒清理，不存在逐请求全扫/全量压缩。
- Desktop Pet 在任何 durable security I/O 前取得 Electron single-instance lock。全局 admission 与最多 4,096 个 TTL/LRU peer bucket 共同限制 pre-auth；action 只有在 audit/nonce 成功后才进入 renderer。
- WeChat 的 replay 检查、credential-global token 消费与 nonce/command claim 在同一个 `BEGIN IMMEDIATE` 事务中；并发 duplicate 不再额外消耗 token。实际 callback URL、queue/result/ledger/audit 都绑定同一 captured config。
- 所有 audit 均验证既有 current/archives、终止 LF 和 JSON 行；partial/short write 会永久 poison 当前 writer。Python/Node 路径族拒绝 audit `.1..8`、SQLite sidecars、大小写/realpath/hardlink/junction alias；C# 拒绝 canonical `.1..8` 与 final reparse，parent-junction/hardlink 作为本地 operator P2 约束登记。审计只记录摘要和 authority 元数据，不记录 secret、正文或联系人。
- WeChat JSONL 每行最多 256 KiB、每轮最多 256 行/4 MiB；超长无换行记录使用跨 cycle 丢弃状态，每轮有确定 I/O 上限且不会把中段解析为新命令。
- C# effect queue、每帧 drain 和 datagram/body 均有上限；blendshape/chibi 必须唯一命中 humanoid avatar，accessory 必须命中显式 allowlist 中的唯一精确相对路径，不做模糊窗口/Avatar fallback。

## 验证结果

| 层 | 结果 |
|---|---|
| focused unit/owner/security wave | `86 passed in 4.00s` |
| TASK-0494 integration | 首次且仅运行一次，`1 passed in 0.95s` |
| C# compile | `0 errors`；clean changed build 的 28 条 warning 均为既有 Unity/程序集/deprecation/JsonUtility 诊断 |
| static checks | Python `py_compile`、Node `--check`、`git diff --check` 通过 |
| 独立复审 | Mate、Desktop、WeChat 三路最终 P0/P1 均清零 |

唯一 integration 在临时目录中构造三套专属测试 credential 和 durable store，以 recording handler、recording wx adapter 及无 listener 的 Node security gate 串联 Mate、Desktop Pet、WeChat。每条签名效果首次成功；重建 authority 后相同请求均被持久 replay authority 拒绝，第二次 effect 为零。测试不绑定 socket、不启动 Electron/MateEngine/微信、不访问网络、不操作真实窗口或设备。

独立复审在收口前实际发现并推动修复：principal/scope/source 轮换放大 rate、并发 replay 额外扣 token、未来 timestamp replay 窗口、audit 断尾/short write、audit/replay alias、pre-auth 状态膨胀、超长 JSONL 单 cycle 无界 drain、实际 URL/queue/result 未绑定 config，以及 C# replay 大集合逐请求压缩。最终三位 reviewer 均明确返回无剩余 P0/P1。

## 为什么还不能标 DONE

本任务完成了 owned bridge 的安全内核和默认关闭，但没有越过受保护 producer/startup/verifier：

1. `host_brain/channels/mateengine.py` 仍发送 unsigned HTTP command，并保留 raw local UDP `32145` signal/text writer；新的 ingress 会稳定拒绝 unsigned HTTP，但 raw sink 尚未纳入签名 authority。
2. `scripts/runtime/start_host_side.ps1` 尚未从一个冻结配置传入专属 ingress/plugin credential、audit/replay 路径，也未先消费 TASK-0492 Service Catalog reservation。
3. `edge_client/channels/wechat_command_queue.py` 仍写 legacy unsigned row；新的 wxautox4 authority 会拒绝这些 row，故不能声称真实 outbound 已切流。
4. `edge_client/gateway/bootstrap.py` 的 WeChat verifier 仍使用 generic Gateway HMAC key，尚未接专属 `NATSUME_WECHAT_GATEWAY_CALLBACK_HMAC_SECRET`。
5. Service Catalog 的真实 startup/reservation、C# plugin 部署及 Electron/MateEngine/WeChat live construction/cutover 未获授权，也未执行。

这些缺口当前全部 fail closed，而不是兼容性降级。protected handoff 完成、construction/cutover tests 通过前，TASK-0494 必须保持 `IMPLEMENTED_AWAITING_PROTECTED_HANDOFF`。

## Rollback

1. 设置 `NATSUME_BRIDGE_EXPOSURE_MODE=DISABLED`，并保持 `HOST_MATEENGINE_BRIDGE_ENABLED=false`、`EDGE_WECHAT_BRIDGE_ENABLED=false`、`NATSUME_DESKTOP_PET_BRIDGE_ENABLED=false`。
2. 只停止 candidate bridge；不得 kill/adopt incumbent listener，不得重定向 MateEngine 窗口或微信会话。
3. 保留 audit/replay 证据；不得删除 nonce/command claim 来重试可能已经完成的 effect。
4. protected producer、verifier、startup 与 Service Catalog handoff 完成前，只使用 recording/no-device adapter。

TASK-0495 可在隔离 profile 中消费本任务安全门做 P0 golden/negative/fault matrix，但不得把隔离通过解释成 production cutover；真实统一实测仍留到全部重构任务完成后的最终程序验收。
