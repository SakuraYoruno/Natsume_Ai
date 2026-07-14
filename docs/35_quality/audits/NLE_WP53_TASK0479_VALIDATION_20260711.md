受众：PROJECT
标题：TASK-0479 ScenePolicy 与 ActionArbiter 验证报告

# TASK-0479 / LTE-WP-53 验证报告

版本：v1.1
日期：2026-07-11
状态：`PASSED`
基线提交：`dbc6d8e62`

## 1. 结论

TASK-0479 已新增可过期 `Natsume.scene.decision.v1`、`Natsume.governed_action.binding.v1`、只收紧的 `Natsume.action_arbiter.decision.v1` 与 `Natsume.action.preview.v1`。SceneDecision 继承上游 source、confidence、observed/expires、TTL、epistemic/conflict，不在每次读取时续期；Mapping 和对象路径都按 `expires_at_ms` 判过期。它已同时接入 ProactiveVoice 和 Surface `must_not_do`，只限制表达/追问/主动性，不修改已解析的用户 task control。

ActionArbiter 不再信任松散 proposal。Governor APPROVE 现在携带脱敏的 intent/action/scope/static-risk/approval-token digest binding；route-token validator 要求 ref/detail 同时存在且一致，只有 `GOV_OK_RENDER/CACHED/RULE` 可执行，AIRLOCK 固定 `PAUSED_REVIEW`，fallback/secondary/unknown/missing/mismatch 均 fail-closed。Governor 与 ToolBroker 共享进程级 TokenService，canonical skill handler 前先验证 intent/action/session/seq/token，再由 Arbiter 校验 binding/scope/risk/scene，最后一次性消费 token。旧的重复 skill execution 模块已改为 canonical path 代理，不再形成旁路。

AIRLOCK 返回的 approval ref 故意不登记到 TokenService，因此即使绕过聊天路由也不可作为执行 token。Action preview 固定 `DRY_RUN`，不调用 executor、不产生外部副作用；可选写入只含 decision/ref/risk/scope 的 `ACTION_ARBITER_DECIDED` Runtime Event，AIRLOCK 的 `would_execute=false`。

`HOST_ACTION_ARBITER_ENABLED` 是独立执行切流开关，默认 `false`：关闭时兼容完全缺失 route-token pair 的 legacy 输出；一旦提供了非法 pair 仍然 fail-closed，AIRLOCK 无论开关状态都暂停。`HOST_LIFE_PROJECTION_ENABLED=false` 可关闭 Scene 软策略，但不能关闭 Governor route/token 门。

## 2. 变更范围

| 路径 | 动作 | 说明 |
|---|---|---|
| `host_brain/persona/scene_policy.py` | ADD | source-preserving expiring SceneDecision、冲突/TTL 与 proactive adapter |
| `host_brain/persona/proactive_voice.py` | MODIFY | 真实使用 SceneDecision，保留上游观测/过期元数据 |
| `host_brain/app/surface_main_chain_adapter.py` | MODIFY | SceneDecision 投影到 expression-only guardrails，不改 task control |
| `host_brain/app/chat_runtime_input.py`、`vision/agent_loop.py` | MODIFY | scene source/confidence/observed/expires 贯通与捕获时 TTL |
| `host_brain/governor/action_arbiter.py` | ADD | committed route + authority binding + scope/risk/token 单调约束 |
| `host_brain/governor/action_preview.py` | ADD | dry-run preview 与 sanitized Runtime Event adapter |
| `host_brain/governor/decision.py`、`route_token.py` | MODIFY | approved action binding、AIRLOCK 非执行 token、fail-closed route validator |
| `host_brain/app/dependencies.py` | MODIFY | Governor/ToolBroker 共享 TokenService |
| `host_brain/app/chat_handler.py` | MODIFY | GOV_APPROVED/Cortex 前 route gate，AIRLOCK 暂停 |
| `host_brain/app/executive_runtime_core.py` | MODIFY | handler 前 binding/registry/token/Arbiter 门与一次性 token 消费 |
| `host_brain/app/executive/skill_execution.py` | CONVERGE | 删除不完整重复实现，代理 canonical governed path |
| `tests/contract/test_nle_scene_action_arbiter_contract.py` | ADD | T-NLE-17 对抗合同与真实 handler token replay 验证 |
| `tests/unit/test_task0479_route_token_gate.py` | ADD | route pair、AIRLOCK、chat cutover、共享 token authority 验证 |

## 3. 验证证据

- T-NLE-17：`12 passed`，覆盖 source TTL 不续期、ProactiveVoice/Surface 实际影响、真实 Governor binding、AIRLOCK、跨 intent/action/scope 复用、risk floor、token bypass/replay、explicit goal、preview/Runtime Event 和 disabled fallback。
- route-token/chat/shared-token 专项与 Governor/Token 合同组：`46 passed`。
- 真实 skill execution、tiered governance、ToolBroker 回归组：`81 passed`。
- Surface、chat runtime、vision scene、ProactiveVoice 与 chat streaming 回归组：`80 passed`。
- 合计本轮聚焦/相关回归：`207 passed`；14 个变更 Python 模块 `py_compile` 通过；`git diff --check` 通过。
- 独立只读阻塞项复审：`PASS`；AIRLOCK、authority binding、handler 前门、Surface goal/TTL 四项均关闭，未发现新增 P0/P1。
- 文档 audience/delivery scoped gates：通过；freshness scoped gate：`3 checked / 3 skipped / 0 failures`。
- 与独立 TASK-0483 合入后的全量 NLE 合同：`206 passed / 17 warnings`；warnings 均为既有 invalid-escape deprecation。

## 4. 停止条件与回滚

此前审查发现的四项阻塞已关闭：AIRLOCK 不再可执行；Governor 批准绑定实际 intent/action/scope/static risk/token；`user_goal_preserved` 由 binding 匹配计算且 Surface 不改 task；Scene TTL 继承来源且 Mapping 路径重算过期。未发现 Arbiter 放宽 token/scope/risk、scene 覆盖用户显式目标、preview 执行副作用或旧 execution helper 旁路。

回滚时保持 `HOST_ACTION_ARBITER_ENABLED=false`，即可保留 legacy 缺 route-pair 输入的兼容路径；`HOST_LIFE_PROJECTION_ENABLED=false` 关闭 scene 表达投影。两种回滚都不会把 AIRLOCK、非法 route pair 或 Governor REJECT 变为可执行，也不会恢复重复 handler 旁路。
