受众：PROJECT
标题：TASK-0476 Digital Life OCC 与 Schema Migration 验证报告

# TASK-0476 / LTE-WP-50 验证报告

版本：v1.0
日期：2026-07-11
状态：`PASSED`
基线提交：`09b3e7f`

## 1. 结论

TASK-0476 已将 `digital_life_state` 的保存路径从无条件 upsert 改为 expected-version CAS。新增 `LifeStateCommandService` 作为薄 command facade，写入时必须携带 `expected_version`；stale expected version 返回 `CONFLICT`，不会覆盖已提交状态。旧直接调用 `_save_state()` 的测试/单写者兼容路径保留为显式 `FALLBACK_APPLIED`，并写入 OCC audit，不再使用旧的 silent `ON CONFLICT DO UPDATE` 覆盖路径。

schema 已新增受控 migration `0057_life_occ.sql`，创建/补齐 Digital Life 基础表、version 索引和 `digital_life_state_occ_event`。本任务未迁移生产副本；生产副本迁移仍需备份与 dry-run 报告批准。

## 2. 决策边界

- `ensure_life_schema()` 继续保留 in-memory/legacy 测试 bootstrap；正式持久库结构通过 migration catalog 管理。
- CAS 成功路径使用 `UPDATE digital_life_state ... WHERE person_id=? AND version=?`，以 SQLite rowcount 判定是否发生版本冲突。
- command facade 冲突返回结构化 `CONFLICT` 结果并记录 `digital_life_state_occ_event`，不抛出未处理异常给调用方。
- `_save_state(expected_version=None)` 仅作为显式 single-writer fallback；该路径会写 `FALLBACK_APPLIED` audit，不能作为多 writer 并发入口。
- 生产副本未执行 migration；不触发任务卡的生产迁移审批边界。

## 3. 变更范围

| 路径 | 动作 | 说明 |
|---|---|---|
| `host_brain/runtime/life_continuity.py` | MODIFY | OCC 常量/异常、CAS `_save_state()`、OCC audit、`LifeStateCommandService`、内部生命周期保存路径补 `expected_version` |
| `host_brain/audit/migrations/0057_life_occ.sql` | ADD | Digital Life schema catalog 扩展、OCC event 表、version/person 索引 |
| `tests/contract/test_nle_life_occ_contract.py` | ADD | T-NLE-14 合同测试 |
| `docs/20_prd/PRD.md`、`docs/30_devspec/*`、`docs/60_trace/requirements_trace.md` | MODIFY | TASK-0476 状态、FR-NLE-004 追踪和当前执行入口同步 |

## 4. 验证证据

- T-NLE-14 聚焦合同：`4 passed`，覆盖 migration 幂等、旧数据保留、stale expected version 冲突不覆盖、20 并发 expected-version command 仅 1 个 APPLIED / 19 个 CONFLICT、显式 single-writer fallback audit。
- 既有 Life runtime 回归：`7 passed`，覆盖 temporal integrator、runtime health、digital life causality trace。
- 旧观测/对话闭环兼容回归：`23 passed`，覆盖 proactive autonomy snapshot、surface memory fog、autonomous self dialogue closure。
- Migration runner 合同：`5 passed`。
- M48-A NLE 合同族：`175 passed`，仅出现既有 11 个 DeprecationWarning。
- `py_compile`：`host_brain/runtime/life_continuity.py` 通过。
- `git diff --check`：通过。

## 5. 回滚

如需回滚并发写入口，应禁用 `LifeStateCommandService` 调用方并退回单写者模式；保留 `0057_life_occ.sql` 的 schema 扩展和 OCC audit 表，不恢复旧的无条件多写 upsert。生产副本迁移尚未执行，因此无需生产数据回滚。
