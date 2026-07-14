受众：PROJECT
标题：TASK-0477 WorldProjection 只读投影验证报告

# TASK-0477 / LTE-WP-51 验证报告

版本：v1.0
日期：2026-07-11
状态：`PASSED`
基线提交：`6cd5038`

## 1. 结论

TASK-0477 已新增只读 `WorldProjection` read model。`host_brain/runtime/world_model.py` 生成 `Natsume.world_projection.v1` snapshot，从 environment、`digital_life_state`、work events、runtime health 与可选 StateArbiter fact keys 聚合 facts；每条 fact 均携带 source、source_ref、confidence、observed_at_ms、expires_at_ms、ttl_ms、epistemic_type 与 conflict_state。

Projection 不写 Life/Canon/Memory，不创建新真相源。空库读取不会初始化 `digital_life_state`；API 仅暴露 `GET /v1/digital-life/world/snapshot`，没有 world 写路由或 write facade。

## 2. 决策边界

- WorldProjection 是 query/read model，不是 command model；不得用于提升 Canon、写 Memory 或覆盖 Life state。
- `get_life_state()` 会初始化 state，因此 projection 内部改为直接只读查询 `digital_life_state`；无 state 时返回 degraded fact。
- inferred facts 使用低置信度与短 TTL；observed facts 仍带 TTL，过期后 conflict_state 标记为 `EXPIRED`。
- StateArbiter 冲突只作为 `CONFLICT` fact 暴露，不自动改写 winner/loser 来源。
- `HOST_LIFE_PROJECTION_ENABLED=false` 时返回 read-only disabled snapshot，不影响来源状态。

## 3. 变更范围

| 路径 | 动作 | 说明 |
|---|---|---|
| `host_brain/runtime/world_model.py` | ADD | 只读 WorldSnapshot read model、fact builders、StateArbiter adapter、read-only facade |
| `host_brain/app/digital_life_routes.py` | MODIFY | 新增 `GET /v1/digital-life/world/snapshot`；无写路由 |
| `tests/contract/test_nle_world_projection_contract.py` | ADD | T-NLE-15 合同测试 |
| `docs/20_prd/PRD.md`、`docs/30_devspec/*`、`docs/60_trace/requirements_trace.md` | MODIFY | TASK-0477 状态、FR-NLE-004 追踪和当前执行入口同步 |

## 4. 验证证据

- T-NLE-15 聚焦合同：`6 passed`，覆盖空库 projection 不初始化 Life state、fact 字段完整、inferred fact 低置信/短 TTL、过期 work fact 可见、StateArbiter conflict 可见、world 写路由/write facade 不存在、disabled snapshot 只读。
- StateArbiter / Digital Life 相关回归：`13 passed`。
- M48-A NLE 合同族：`181 passed`，仅出现既有 11 个 DeprecationWarning。
- `py_compile`：`world_model.py`、`digital_life_routes.py` 通过。
- `git diff --check`：通过。

## 5. 回滚

可通过 `HOST_LIFE_PROJECTION_ENABLED=false` 关闭 snapshot 内容输出；保留 GET read API 返回 disabled read-only snapshot。由于本任务没有新增持久表、没有写入 Canon/Memory/Life state，因此回滚不需要数据迁移。
