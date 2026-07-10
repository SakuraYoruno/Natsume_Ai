受众：CODEX
标题：NLE WP-20 / TASK-0466 Canonical Contracts 验证报告

# NLE WP-20 / TASK-0466 Canonical Contracts 验证报告

版本：v1.0
日期：2026-07-10
状态：`PASSED`
任务：`TASK-0466 / LTE-WP-20`
基线提交：`030ad1f98`
实现提交：本报告所在 `TASK-0466` 单任务提交

## A. 行为变化

1. 保留 `AgentWorkOrder` v2 原字段、API 与序列化，新增独立 `WorkOrderV3` 与双向 adapter。
2. v2 payload 可 exact round-trip；v3 投影到 v2 metadata 后可恢复完整 lineage、revision、attempt、budget、幂等与 review refs；未知版本 fail closed。
3. 新增纯合同 `TaskGraph`、`TaskNode`、`TaskEvent`，循环依赖和非法状态迁移均返回稳定 reason code。
4. 新增 `VerificationResult`、`ReviewItem`、Budget policy/reservation 与 SubAgentSpec/Request adapter。
5. 新增 deterministic schema snapshot；`SkillDagPlan` 只增加 lazy compatibility re-export，不改变现有规划或执行逻辑。

## B. 文件与范围

| 文件/目录 | 动作 | 说明 |
|---|---|---|
| `host_brain/agent_runtime/work_order.py` | MODIFY | v2 保留；新增 canonical v3 与 adapters |
| `host_brain/agent_runtime/task_graph.py` | ADD | TaskGraph/Node/Event 与状态机 |
| `host_brain/agent_runtime/verification_policy.py` | MODIFY | 新增 VerificationResult；既有 runner 不变 |
| `host_brain/agent_runtime/review_policy.py` | MODIFY | 新增 ReviewItem；既有 policy 不变 |
| `host_brain/agent_runtime/budget_policy.py` | ADD | Budget policy/reservation 纯合同 |
| `host_brain/agent_runtime/sub_agent_contract.py` | ADD | Child WorkOrder → SubAgentRequest adapter |
| `host_brain/agent_runtime/contract_errors.py` | ADD | 稳定 reason codes |
| `host_brain/agent_runtime/contract_schema.py` | ADD | schema snapshot builder |
| `host_brain/app/skill_dag_planner.py` | MODIFY | lazy compatibility re-export |
| `tests/contract/test_nle_canonical_contracts.py` | ADD | T-NLE-04 |
| `tests/fixtures/nle_contracts/canonical_contracts.v1.json` | ADD | frozen snapshot |

确认：未新增 TaskStore、scheduler、lease、outbox 或主链开关；未修改 VerificationPolicy 裸命令执行行为，该治理仍属于 TASK-0470。

## C. 测试证据

| 测试组 | 结果 | 关键断言 |
|---|---:|---|
| T-NLE-04 新契约 | 24 passed | v2/v3 双向 round-trip、版本拒绝、cycle/非法迁移、snapshot |
| WorkOrder/RuntimePolicy/SkillDag/规划/SubAgent | 36 passed | 既有公开 API 与行为不回退 |
| Agent Runtime/Skill wrappers/Internal routes | 26 passed | import/export 与执行适配不回退 |
| 合计 | 86 passed | 0 failed |
| Static scan | passed | 0 violations |
| Compile/diff | passed | compileall 及 whitespace 检查通过 |

## D. 风险、停止条件与回滚

- v3→v2 adapter 会在 v2 metadata 增加 canonical 字段与 `_work_order_v3_metadata` 保真标记；只有显式调用新 adapter 才出现，现有 v2 主链不变。
- 状态机与 snapshot 已冻结；后续 TaskStore/scheduler 必须消费这些合同，不得自行增加非法捷径。
- 未命中停止条件：v2 字段、公开 API 和现有 planner 均未删除或破坏。
- 回滚：删除新增纯合同文件并撤销 re-export；`AgentWorkOrder` v2 与旧执行逻辑可保持原状。

## E. 自动推进裁决

- [x] T-NLE-04 与兼容回归通过。
- [x] TASK-0466 可标记 DONE 并按单任务提交。
- [x] TASK-0467、TASK-0468 依赖 Gate 已解锁；按完整重构 Goal 自动推进。
