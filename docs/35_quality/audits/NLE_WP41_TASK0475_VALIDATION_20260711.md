受众：PROJECT
标题：TASK-0475 Context 引用授权与 Token 预算验证报告

# TASK-0475 / LTE-WP-41 验证报告

版本：v1.0
日期：2026-07-11
状态：`PASSED`
基线提交：`532909de`

## 1. 结论

TASK-0475 沿用既有 `AgentContextEnvelope`，没有新建平行 Context Pack。Envelope schema 升级为 `Natsume.sub_agent.context.v2`，新增 `context_refs`、`authorization`、`token_budget` 与 `pollution_report`。默认授权 profile 允许 task-local 与 public-derived refs，拒绝 private/raw/secret/token/cookie/credential refs；上下文污染命中后 fail-closed。SubAgentDispatcher 会把 ContextEnvelopeError 转为失败响应，并只在 audit refs 中发布预算、授权、污染和 runtime-event 安全摘要。

## 2. 决策边界

- `memory://raw/*`、`private://*`、`secret://*` 等 ref 默认越权；当前任务没有开放 private context 授权。
- token 统计使用 deterministic local estimator：CJK 字符按 1 token，非 CJK 字符按 4 chars/token 估算；`actual_tokens` 在本阶段表示本地可复现的 envelope actual estimate，不调用外部 tokenizer。
- `context_token_budget` 默认只审计；设置 `enforce_context_token_budget=true` 时超预算 fail-closed。
- Runtime Event 投影只包含 refs、计数、授权摘要和 token budget，不包含 task text、user text、turn content、facts 或 evidence snippets。
- 公共审计键使用 `context_authorization`，避免被通用 sanitizer 误判为 HTTP `Authorization` secret。

## 3. 变更范围

| 路径 | 动作 | 说明 |
|---|---|---|
| `host_brain/app/sub_agent_context.py` | MODIFY | ContextEnvelope v2、ref authorization、token estimator、pollution detection、safe runtime event projection |
| `host_brain/app/sub_agent_protocol.py` | MODIFY | ContextEnvelopeError fail-closed 响应、安全 audit refs 扩展 |
| `tests/contract/test_nle_context_envelope_contract.py` | ADD | T-NLE-13 合同测试 |
| `docs/20_prd/PRD.md`、`docs/30_devspec/*`、`docs/60_trace/requirements_trace.md` | MODIFY | 状态与追踪同步 |

## 4. 验证证据

- T-NLE-13 聚焦合同：`8 passed`，覆盖 refs 授权、private/raw refs 拒绝、污染拒绝、强制 token budget 超限拒绝、runtime event 安全投影、dispatcher 安全 audit refs 与拒绝响应。
- 既有 sub-agent context/protocol/translate 回归：`25 passed`，确认 bounded envelope 与 legacy context projection 不回归。
- Planner/SubAgent adapter 与 canonical contracts：`58 passed`。
- TASK-0475 相关组合：`67 passed`，覆盖 T-NLE-13、sub-agent context/protocol/translate 与 planner adapter。
- M48-A 主要 NLE 合同组合：`135 passed`，覆盖 canonical contracts、Planner adapter、TaskStore、Verification、Acceptance、Review、Runtime cutover、Runtime Event 与 Context Envelope。
- 文档 freshness / NLE boundary / migration runner：`56 passed`，仅出现既存 pentest/persona regex DeprecationWarning。
- `py_compile`：`sub_agent_context.py`、`sub_agent_protocol.py`、T-NLE-13 tests 通过。
- `git diff --check`：通过。

## 5. 回滚

回滚时可让调用方继续使用已有 bounded envelope 字段；新增 `context_refs`、`authorization`、`token_budget`、`pollution_report` 为扩展字段。不得回滚到 raw history/raw retrieval rows 直传子 Agent；如需禁用新增来源，应禁用对应 context ref 生产方，而不是放宽 private/raw refs。
