受众：CODEX
标题：NLE WP-01 / TASK-0462 Trace 数据最小化验证报告

# NLE WP-01 / TASK-0462 Trace 数据最小化验证报告

版本：v1.0
日期：2026-07-10
状态：`PASSED`
任务：`TASK-0462 / LTE-WP-01`
基线提交：`030ad1f98`
实现提交：本报告所在 `TASK-0462` 单任务提交

## A. 行为变化

1. 新增集中式 `nle.trace.redacted.v1` 合同；durable/propagated Trace 仅保留 decision summary、input/output/evidence refs、payload hash 与脱敏元数据。
2. `AgentStep` / `ReactStep` 的 raw thought 只供进程内循环使用，不再进入 AgentResult metadata、scratchpad、JSON artifact、job API、async stream 或 SubAgent audit。
3. 新增独立 `agent_output` 公开结果通道；正常长结果完整保留，secret/token/cookie/private-memory 正文只返回安全提示与 SHA-256 引用。
4. 历史 Trace 继续兼容读取，但必须先经集中脱敏，禁止重新公开旧 `thought`/敏感正文。
5. Agent artifacts、Internal Job API、SubAgent dispatch 与相关 handlers 统一经过同一公开合同。

## B. 文件与范围

| 文件/目录 | 动作 | 说明 |
|---|---|---|
| `host_brain/core/trace_redaction.py` | ADD | 固定 allowlist、hash/ref、legacy compatibility、安全输出 |
| `host_brain/agent_runtime/agent_executor.py` | MODIFY | raw thought 仅 ephemeral；metadata 写 redacted trace |
| `host_brain/agent_runtime/result_contract.py` | MODIFY | public/durable serializer 集中脱敏 |
| `host_brain/agent_runtime/executor.py` | MODIFY | result/metadata artifact 写入门禁 |
| `host_brain/app/agents/react_loop.py` | MODIFY | scratchpad/stream 固定公开合同；独立 output envelope |
| `host_brain/app/sub_agent_protocol.py` | MODIFY | 新输出优先、历史兼容、安全 audit digest |
| `host_brain/app/internal_agent_routes.py` | MODIFY | 历史 job API 不重放 raw thought |
| `host_brain/integrations/graph_subgraph.py` | MODIFY | artifact summary/ref/hash 边界 |
| 8 个 Agent handlers | MODIFY | 公开输出与手工 scratchpad 统一治理 |
| `tests/contract/test_nle_trace_minimization_contract.py` | ADD | T-NLE-12 |
| `tests/fixtures/nle_trace_leakage_cases.json` | ADD | secret/cookie/private-memory 负例 |

确认：未清理历史数据，未新增 Runtime Event 表、SSE cursor 或完整 Trace UI。

## C. 测试证据

| 测试组 | 结果 | 关键断言 |
|---|---:|---|
| 新 Trace 合同 + 修改过的执行/Pentest tests | 92 passed | AgentExecutor、scratchpad、secret fixtures、hash、历史兼容 |
| Handler/SubAgent/Internal route 回归 | 111 passed | 长输出不截断、stream 全部 redacted、audit 不泄漏 |
| 合计 | 203 passed | 0 failed |
| Static scan | passed | 0 violations |
| Canon/生成数据检查 | clean | 无额外运行数据进入工作树 |
| Diff check | passed | 无 whitespace error |

## D. 风险、停止条件与回滚

- `react_scratchpad` 不再包含 final answer/action/observation 正文；已知消费者均改读 `agent_output` 或结构化 Trace 字段。
- Async stream 从内部对象收紧为公开 dict；仓库内无其它消费者，未登记外部调用者需按新合同适配。
- 敏感模式采用保守匹配，形如 `token=...`、`Cookie: ...` 的普通文本也会被引用化，这是预期 fail-closed 行为。
- 未命中停止条件：内部 ReAct 仍可使用 ephemeral thought，现有消费者无需依赖 durable raw thought。
- 回滚：可回退 adapters/handlers，但不得恢复 raw thought 新写入；legacy compatibility 与泄漏门禁必须保留。

## E. 自动推进裁决

- [x] T-NLE-12 与兼容回归通过。
- [x] TASK-0462 可标记 DONE 并按单任务提交。
- [x] TASK-0469 的 Trace 前置 Gate 已满足；其余前置仍按 TASK-0467/0468 推进。
