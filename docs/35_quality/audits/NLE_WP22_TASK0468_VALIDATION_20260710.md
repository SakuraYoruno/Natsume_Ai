受众：CODEX
标题：NLE WP-22 / TASK-0468 Planner Adapters 验证报告

# NLE WP-22 / TASK-0468 Planner Adapters 验证报告

版本：v1.1
日期：2026-07-10
状态：`PASSED`
任务：`TASK-0468 / LTE-WP-22`
基线提交：`b2b2ae4e45c3aecb298912e44c8ca09d961e8a0e`
实现提交：本报告所在 `TASK-0468` 单任务提交

## A. 行为变化

1. 新增纯 `PlannerAdapter` 合同；PlanningAgent 与 SkillDag 只能消费调用方分配的同一 `graph_id/revision/root_work_order_id`，adapter 不生成第二 graph identity。
2. Canonical `TaskGraph` 同步保留顺序、并行与混合 DAG；每条依赖生成稳定 artifact ref，循环依赖以 `PLAN_CYCLE` fail closed。
3. `PlanGraph` 可读取上游 canonical plan 或原 SkillDag，不调用第二次 PlanningAgent；当前仅生成 shadow comparison、持久化输入和依赖批次，不执行 skill、SubAgent 或 synthesize LLM。
4. Delegate 节点先由 canonical graph node 构造 Child WorkOrder，再经 `SubAgentRequestAdapter` 和只读 registry view 投影到 legacy request shape；root/parent/graph/revision/node lineage 不可缺失。
5. 新开关 `HOST_CANONICAL_PLANNER_ADAPTER_ENABLED` 默认关闭；关闭时 legacy PlanGraph 输入、执行与公开 API 不变，开启时 shadow-only 并以 `CANONICAL_PLANNER_SHADOW_ONLY` 阻止误当成真实切流。
6. Inline execution args 未转换为 governed `input_refs` 时拒绝适配，避免在 Trace/TaskStore 治理完成前扩张持久 payload 或伪造上下文。
7. Canonical `TaskGraph`、planner nodes 与 deterministic batches 改为单向派生并执行完整 execution-semantic digest 对比；provenance 可不同，但 risk、contract、retry、route、target、依赖或并行语义任一漂移均不再误报兼容。
8. Child WorkOrder 将 root/parent/child、graph revision、node、route、target 与 capability 绑定为一个稳定 digest；parent 已绑定其他 graph/revision、request 重定向其他 agent、删除 target capability 或重放其他 parent lineage 均 fail closed。
9. SkillDag 的 `parallel_window` 与合法 `execution_batches` 保真；canonical adapter 按 `(step_index, node_id)` 的既有调度顺序验证，cycle warning 或批次漂移直接拒绝。
10. PlanningAgent 的 `ask`、step `NEEDS_INPUT` 与 plan `needs_input` 统一返回稳定原因 `PLANNER_NEEDS_INPUT`，不再生成零节点/可误执行图。
11. Canonical shadow 对外仅保留 lineage、artifact ref、digest、route binding 与 batches；Child WorkOrder、SubAgentRequest、任务描述、input pack、metadata、用户消息不进入 shadow/Executive payload，并由真实 secret scanner 回归覆盖。

## B. 文件与范围

| 文件 | 动作 | 说明 |
|---|---|---|
| `host_brain/agent_runtime/planner_adapter.py` | ADD | graph revision identity、planner views、DAG validation、shadow diff、Child WorkOrder adapter |
| `host_brain/agent_runtime/contract_errors.py` | MODIFY | 新增稳定 fail-closed 原因 `PLANNER_NEEDS_INPUT` |
| `host_brain/agent_runtime/sub_agent_contract.py` | MODIFY | canonical child lineage/route/target/capability binding 验证 |
| `host_brain/agent_runtime/__init__.py` | MODIFY | additive canonical planner exports |
| `host_brain/app/skill_dag_planner.py` | MODIFY | lazy compatibility re-export；legacy builder 不变 |
| `host_brain/app/plan_graph.py` | MODIFY | existing-plan shadow consumer、dependency refs、无副作用结果 |
| `host_brain/app/executive_runtime_core.py` | MODIFY | 将既有 plan/可选 parent WorkOrder 传给 opt-in adapter；默认路径不变 |
| `host_brain/app/sub_agent_protocol.py` | MODIFY | read-only registry view 与 Child WorkOrder request bridge |
| `host_brain/app/settings/core.py` | MODIFY | 默认关闭的 canonical planner adapter flag |
| `tests/contract/test_nle_planner_adapter_contract.py` | ADD | T-NLE-05 契约、边界与 legacy regression |
| `docs/35_quality/audits/NLE_WP22_TASK0468_VALIDATION_20260710.md` | ADD | 本验证与追踪证据 |

范围确认：未新增 Worker scheduler、TaskStore authority、lease/retry/CAS、数据库迁移、主链切流或新外部副作用；未删除旧 PlanningAgent、SkillDagPlan、PlanGraph、SubAgentRequest 公开 API。

## C. T-NLE-05 追踪

| 任务断言 | 代码证据 | 测试证据 |
|---|---|---|
| 只规划一次 | `prepare_existing_plan_graph_state()` 只解析上游 plan；缺失上游时 fail closed | canonical/upstream SkillDag 用例将 legacy planner 替换为必失败函数，调用数为 0 |
| 同一 graph revision | `GraphRevisionIdentity`、`validate_same_graph_revision()`、serialization boundary round-trip | same ID/revision、revision mismatch 与 payload round-trip 用例 |
| 完整 semantic equivalence | canonical nodes 单向派生 TaskGraph/batches；完整 execution semantic digest，provenance 不参与等价判断 | risk/input/contract 与 batch tamper、等义不同 provenance、非等义相同 route/dependency 用例 |
| 顺序/并行/混合依赖 | canonical nodes、topological execution batches、stable dependency refs | sequential、parallel、A→{B,C}、mixed join、explicit nonparallel 用例 |
| cycle fail closed | canonical `TaskGraph.validate()` 与 adapter batch guard | `PLAN_CYCLE` 用例 |
| dependency_context 非空 | dependent shadow task 使用 stable artifact refs 构造 context | PlanGraph shadow dependent task 断言 |
| Child WorkOrder lineage | `build_child_work_order_for_plan_node()` → `build_sub_agent_request_from_child_work_order()` | root/parent/graph/revision/node、root request rejection 用例 |
| Target/capability binding | child lineage digest + registry target + capability requirement 三重校验 | cross-agent redirect、capability deletion、parent lineage replay 用例 |
| SkillDag 保真 | `parallel_window` 保留并按 source scheduler order 验证 execution batches | window=1、真实 builder 乱序 step、batch drift 用例 |
| NEEDS_INPUT | ask/step status/plan status 使用稳定 `PLANNER_NEEDS_INPUT` | ask-only、ask dependency、两类 status marker 用例 |
| registry view | `get_sub_agent_registry_view()` 与 fail-closed resolver | 排序、唯一性、capability 完整用例 |
| legacy 不回退 | flag 默认 false；旧 API 保留 | flag-off legacy graph、SkillDag、SubAgent、Executive 与 settings 回归 |
| shadow 无副作用/无敏感载荷 | canonical branch不调用 route、dispatcher、skill 或 synthesize；只返回 lineage/ref/digest 投影 | `external_side_effect_count == 0`；PlanGraph 与 Executive shadow 经 secret scanner 验证无 API key、私有 metadata、input pack、user request |

## D. 验证证据

| 验证组 | 结果 | 关键覆盖 |
|---|---:|---|
| T-NLE-05 新契约 | 34 passed | 完整 semantics、DAG/revision、deterministic batches、target/capability/lineage、NEEDS_INPUT、secret-free shadow、legacy flag |
| Canonical + Planner + SubAgent + Executive + Settings 关联矩阵 | 104 passed，15 subtests passed | T-NLE-04、legacy planner、SubAgent context、Executive smoke、settings readiness；包含上述 34 项 |
| Warning-as-error | 34 passed | 新 T-NLE-05 无新增警告或弃用告警 |
| Static scan | passed | 0 violations |
| NLE boundary scan | passed | 0 new violations；仅报告 TASK-0461 冻结 existing debt |
| Compile | passed | 9 个关联实现模块与 T-NLE-05 测试可导入/编译 |
| Audience / delivery doc gate | passed | 验证报告 audience/title 与定向 delivery gate 通过 |
| Diff check | passed | 0 whitespace errors |
| Baseline control probe | known baseline failure | 在临时 detached worktree 的原提交 `c166ad5bb` 单跑 `tests/unit/test_executive_runtime_entry.py`，同样 1 failed；不是本修复引入 |

## E. 风险、停止条件与回滚

- Canonical planner 当前是 shadow-only。返回 `CANONICAL_PLANNER_SHADOW_ONLY` 属于预期 fail-closed 行为，不代表 Worker 已切流；TASK-0469 之前不得把该结果宣称为已执行。
- 上游 plan 缺失、delegate parent lineage 缺失、graph revision 漂移、cycle 或 opaque inline args 均停止，不回退到第二次规划，也不伪造 artifact ref。
- `allow_parallel` 只表达源显式串行限制；dependency readiness 与并行许可分离，A 完成后 B/C 可同批，源显式 `allow_parallel=False` 才拆批。
- Shadow 投影不建立新的 durable trace schema，仅使用 lineage/ref/digest/batch 字段；因此未来合并 TASK-0462 Trace 最小化时无需兼容当前 raw WorkOrder、SubAgentRequest 或 task description payload。
- 额外执行 `tests/unit/test_executive_runtime_entry.py` 时存在 1 个既有 monkeypatch bridge 失败。对照验证直接在未修改原提交 `c166ad5bb540fab52f8bb6a14a2e8a0e2c9b5401` 的临时 detached worktree 单跑，结果仍为 `1 failed`：测试先从 `host_brain.app.executive_runtime` 导入 `executive_runtime_sse_generator`，该符号在模块导入时已绑定为 `_executive_runtime_core_impl`；随后只 monkeypatch `host_brain.app.executive_runtime_core._executive_runtime_core_impl` 不会替换测试持有的旧 callable，因而实际 legacy chat core 运行并返回 `EXECUTIVE_ROUTE` 与最终事件共 2 条。该路径默认未启用 canonical planner，未进入本次新增 route binding、Child/SubAgentRequest binding 或 shadow projection；故未纳入 TASK-0468 门禁计数，也未在本任务越界修复。
- 回滚：关闭 `HOST_CANONICAL_PLANNER_ADAPTER_ENABLED` 即恢复 legacy 主路径；adapter、测试和 shadow payload 可保留，不需要数据库回滚。
- TASK-0469 仍须等待 TASK-0462、TASK-0467 与本任务全部完成后才能按依赖 Gate 推进。

## F. 封板裁决

- [x] T-NLE-05、关联回归、static、compile 与 diff gate 通过。
- [x] 未命中任务停止条件；受治理 refs 缺失的输入按设计 fail closed。
- [x] TASK-0468 状态与 trace 已同步至 PRD、DevTasks、Master、M48-A 执行文档、requirements trace 与 audits index。
