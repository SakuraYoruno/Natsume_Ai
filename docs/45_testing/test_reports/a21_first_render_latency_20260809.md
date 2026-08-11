# A21 纯文本首句延迟自动评测与修复报告

- 日期：2026-08-09
- 分支／基准 HEAD：`dev` / `f1181ae048bff2320385f05b526b9add92b78eea`
- 总结论：`FAIL_EXTERNAL_LATENCY_BOUND`
- 目标：真实首个 `RENDER` p50 ≤ 900 ms，p95 ≤ 1800 ms
- 模型：`deepseek:deepseek-v4-flash`，`thinking=disabled`
- 范围：纯文本 Gateway／Host／Surface／TaskGraph／Acceptance
- 排除：真实 Edge、Voice、Avatar、多模态、Pentest、安全审计和人工盲评

## 1. 结论

30 轮最终真实 `POST /v1/chat` 全部产生了 `RENDER`、`COMMIT` 和
`APPROVE`，且模型与路径分别固定为 `deepseek-v4-flash` 和
`surface_reply:surface_main_chain`。首个真实可见文本 p50/p95 为
2790.39/3899.85 ms，未达到 900/1800 ms。

同一批 30 轮中，Host 审计的 provider first-token p50/p95 已经是
1462.0/2052.6 ms。provider 分段本身即高于完整的首句目标，所以在不更换
模型／供应商服务形态、不用固定占位文本伪造首句的约束下，900/1800 ms
当前物理上不可达。A21 不宣称 PASS。

## 2. 评测器修复

旧 `tools/diag_stream_baseline.py` 仍对已移除的 `GET /v1/chat/stream`
发请求，最初 10/10 的 404 是评测器缺陷，不是产品失败。本轮将它改为：

1. 使用生产合同 `POST /v1/chat` 和 JSON body；
2. 分别记录响应头、首事件、首个 `RENDER`、`COMMIT` 和 server first-token；
3. 缺少 `RENDER` 或 `COMMIT` 时明确失败；
4. 固化逐样本 JSON 证据、模型、路径和最终 decision。

相关评测器单测覆盖正常事件和“只有 COMMIT、没有 RENDER”的失败合同。

## 3. 诊断和修复结果

| 阶段 | 轮数 | first render p50 | first render p95 | provider first-token p50/p95 | 结论 |
|---|---:|---:|---:|---:|---|
| 日语双语 buffered 基线 | 10 | 3496.44 ms | 6052.24 ms | 2159.5/4451.35 ms | FAIL |
| 干净中文 native stream 探针 | 3 | 8874.47 ms | 9735.70 ms | 5521.0/7300.3 ms | 流式传输更慢 |
| 中文 buffered 最终 | 30 | 2790.39 ms | 3899.85 ms | 1462.0/2052.6 ms | `FAIL_EXTERNAL_LATENCY_BOUND` |

DeepSeek 当前 endpoint 的 native streaming 首 delta 显著慢于完整响应。
因此新增 `HOST_SURFACE_NATIVE_SENTENCE_STREAM_ENABLED` 传输开关；关闭时不改变
Surface 权限、质量门禁或模型，只安全回退到 buffered complete。开关默认仍为
`true`，A21 最终运行显式设为 `false`。该修复将探针的约 8.9/9.7 秒降到
最终 2.79/3.90 秒，但无法越过 provider 下界。

30 轮最终分段：

| 分段 | p50 | p95 |
|---|---:|---:|
| HTTP response started | 104.99 ms | 402.29 ms |
| 首个 SSE 事件（非可见占位） | 504.17 ms | 840.75 ms |
| provider first-token | 1462.0 ms | 2052.6 ms |
| 首个真实 `RENDER` | 2790.39 ms | 3899.85 ms |
| `COMMIT` | 2890.03 ms | 4029.64 ms |
| 逐样本 `RENDER - provider first-token` | 1236.83 ms | 1948.90 ms |

首个 SSE 事件不是自然语言正文，不能拿 504/841 ms 冒充首句 PASS。

## 4. Gateway、持久化与质量观察

A21 首轮 Gateway 编排暴露了两个测试环境错误：Host 启动脚本以 `.env` 为
authority 覆盖父进程的临时输入 HMAC，而 Gateway 保留了临时 HMAC；此外 Host
回复目标仍是 `.env` 的 `127.0.0.1:19000`，但 Gateway 一度运行在 41100。
前者导致 Host 401 和 Gateway 8 次重试后 DLQ，后者导致
`surface.reply_delivery` 进入 reconciliation。两项都通过对齐正式配置修复，
失败收据保留，未计为产品通过样本。

对齐后运行两个代表性三轮轨迹，共 6 个真实 Gateway turn：

- 6/6 `INPUT_COMPLETED/OK`、TaskGraph `COMPLETED`、`APPROVE`；
- 6/6 使用 `deepseek-v4-flash`；
- 6/6 Gateway durable input delivery、surface reply outbox／receipt 完整；
- canonical affect writer probe 通过，legacy delta 为 0；
- `task_with_low_mood:1` 自动通过，仍需人工审核；
- `rupture_and_repair:1` 因首轮深路径候选
  `selected_passed=false / benefit_eligible=false` 失败，后两轮和可见回复仍完整。

这个质量失败原样保留。更广的相邻权威质量证据仍是 A20 同一 HEAD／同一
DeepSeek 非思考配置的 48 条轨迹、144 轮：48/48
`AUTOMATED_PASS_HUMAN_REVIEW_PENDING`；它不替代人工盲评，也不覆盖 A21
这次随机候选失败。

## 5. 回归

以下 6 个相关测试文件合计 `252 passed`：评测器、native affective stream、
LLM streaming helper、production provider route 和 Surface runtime。修改文件也已
通过 `py_compile`。

## 6. 证据

| 证据 | SHA-256 |
|---|---|
| 10 轮 buffered 基线 | `7cb20fc6864743141e2d08d2cfed2b525404f5c853491fba7d56d9fc364e4894` |
| 3 轮 native stream 探针 | `5d1407c631b496ad71373ad9176cb41c871f4b6419ba4f7d46367696fc494c54` |
| 30 轮最终首句报告 | `730a94ee47f031f22b30e0de8f76569625bfca15b0746bd28aec67faa407f24d` |
| 2×3 Gateway 最终报告 | `d87aa824e9cfcf438de770a1c28a876999e86d7a906a8b1a2c02bb6c3dd1fc44` |
| A20 48 条／144 轮质量报告 | `9a4720245f9f97b0c86a2fded3d167b67783090c433491794433ed9d6f12b32b` |

原始 A21 根：
`runtime/acceptance/a21-baseline-f1181ae04-20260809-223504/`。

## 7. 后续决策

保持“所有文本模型均为非思考 DeepSeek V4 Flash”时，下一步只能从外部服务形态
入手：更近的 DeepSeek endpoint、可证明等价的低延迟部署、provider 侧 prompt
cache／prefill，或与供应商确认 streaming 路由异常。若 900/1800 ms 是不可放宽的
产品门槛，则需要用户重新授权更快的渲染模型。固定模板、加载提示或空 ACK 都不能
作为首个 `RENDER`。
