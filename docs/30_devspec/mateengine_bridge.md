受众：CODEX
标题：MateEngine 与远端 Edge 当前边界

# MateEngine 与远端 Edge 当前边界

MateEngine 是可选外显运行时，不是人格、情绪、记忆或设备动作的 authority。当前 Host 只保留能力与健康查询，历史直接写接口已经退役。

## 当前 Host API

| API | 语义 |
|---|---|
| `GET /v1/mateengine/capabilities` | 只读能力目录 |
| `GET /v1/mateengine/health` | 只读 Host outbox projection；没有 Edge receipt 时返回未验证 |
| `POST /v1/mateengine/action` | `410 MATEENGINE_DIRECT_COMMAND_RETIRED` |
| `POST /v1/mateengine/signal` | `410 MATEENGINE_DIRECT_COMMAND_RETIRED` |
| `POST /v1/mateengine/say` | `410 MATEENGINE_DIRECT_COMMAND_RETIRED` |

唯一可写路径：

```text
accepted ExpressionPlan
→ Action Preview / governed effect
→ Host durable outbox
→ Edge execution
→ physical receipt
```

Host 入队不等于设备已经动作。没有目标 Edge 的 receipt 时，Monitor 必须显示 `REMOTE_EDGE_UNVERIFIED` 或 `NOT_TESTED`。

## Edge 部署

Edge 位于另一台电脑，承载 ASR、GPT-SoVITS、VTS/VRM 和可选 MateEngine bridge。当前仓库任务不得擅自启动远端进程；只有用户明确允许后才执行物理验收。

历史 MateSignal UDP 协议只可用于 Edge 侧隔离调试，不能绕过 Host 的 ExpressionPlan 和 outbox。模型路径、设备地址和本机设置不写入仓库。

## 验收

- capability/health 查询不得触发设备动作。
- 退役 POST 路由稳定返回 410。
- accepted plan 的 `synchronization_id` 在 Host、Edge 和 physical receipt 一致。
- Edge 断线时文本链可继续，且 canonical self affect 不被重置。
- 重连后 fence 轮换，旧 session 不能继续执行。

远端 Edge 当前未启动，因此物理 TTS、VTS/VRM 和 MateEngine 均为 `NOT_TESTED`。
