受众：CODEX
标题：Natsume AI 通用规划搜索与 Governed Runtime 正式执行合同

# Natsume AI 通用规划搜索与 Governed Runtime 正式执行合同

> 历史快照：GR 执行已结束，本文不再是当前执行合同。其 `PASS` 只覆盖当时声明的 Host 四路线与模拟证据，不能证明 Scheduler/Internal、真实跨领域规划收益或 Full Architecture 完成。

版本：v1.1
日期：2026-07-15
状态：`GR-01～GR-06_LIVE_ROUTE_MATRIX_PASS__FULL_ARCHITECTURE_INCOMPLETE`
代码基线：`origin/dev@31114f7ba9985e99fc82187aeb99b939bbf426ab`

## 1. 唯一目标

恢复在计划迁移中丢失的通用规划搜索需求，并完成通用智能执行层生产切流：

1. 从 Pentest 抽取领域无关的 L1 Graph Search、L2 Cognitive Ranking、L3 Conditional MCTS 为 `PlanningSearchService`。
2. 在 Code 与 Research 两个非 Pentest 固定基准证明相对既有单路径规划的收益。
3. 将选中的搜索路径编译为 immutable、durable、multi-node `TaskGraph`。
4. 执行期禁止二次规划；失败重规划只能创建同一 `graph_id` 的 `revision + 1`，并保留父 revision、失败 attempt 与选择证据。
5. 普通 chat、skill、delegate、plan 全部进入同一 Governed Runtime。
6. 每个节点具备 durable Attempt、Lease、Idempotency Reservation、Budget Reservation、必要的 Review、Verification、Acceptance 与 Receipt。
7. 删除或降级 Executive SkillDag 与 `RuntimeTaskRegistry` 的第二执行真相；对话、Memory、Life 与 Delivery 副作用只能由 Acceptance Outbox 提交。

## 2. 不可被缩减的生产拓扑

```text
Chat / Host Input / Gateway / Scheduler
  -> Secure Ingress + Governor grant
  -> HostCommand
  -> PlanningSearchService (planning only, zero side effects)
  -> immutable TaskGraph(graph_id, revision)
  -> DurableTaskStore
  -> node Attempt + Lease + Idempotency + Budget + Review
  -> governed worker
  -> Verification
  -> Acceptance
  -> transactional Outbox
  -> Receipt + read-only Projection
```

搜索只能提出候选和选择路径，不能执行工具、写 Memory、改变 Persona 或提交外部副作用。执行器只能消费已冻结 graph revision；任何动态改变都必须成为新的 revision。

## 3. 代码事实基线

- `host_brain/app/governed_task_adapter.py` 只接受 `route=skill` 且构造单节点 graph。
- `host_brain/orchestration/governed_command.py`、`host_brain/persistence/host_command_repository.py`、`host_brain/task_runtime/coordinator.py` 均强制恰好一个节点。
- Executive Runtime 独立消费 `SkillDagPlan`，节点失败后停止；retry/idempotency 字段没有完整 durable 消费者。
- cycle warning 在 legacy 路线存在忽略依赖/串行退化风险；新 canonical 路线必须 fail closed。
- `RuntimeTaskRegistry` 是进程内 dict，不是 `DurableTaskStore` projection。
- Budget 与 Review 合同/资产未进入普通 Host Task composition。
- user turn 可早于 governed admission 写入，多个 post-process 仍由 `asyncio.create_task()` 提交。
- Pentest 已有 `l1_graph_search.py`、`cognitive_engine.py`、`llm_mcts_advisor.py`，但接口和状态绑定 Pentest 领域。

## 4. 架构波与完成条件

### GR-00：纠错与全路线覆盖合同

- 将 PR #46 定级为 Core Infrastructure PASS / Full Architecture INCOMPLETE。
- 建立 chat、skill、delegate、plan 的机器可执行覆盖矩阵。
- 状态：`COMPLETED`；覆盖矩阵继续作为后续波次的 fail-closed Gate。

### GR-01：领域无关三层搜索

- 公共 contracts/ports/service 不得 import Pentest。
- L1 负责受约束候选图展开；L2 通过可注入 ranker 进行认知排序；L3 只在风险/不确定性/收益阈值触发，并受 iteration/depth/time/cost 预算约束。
- Pentest 通过 adapter 使用公共能力，不保留第二份公共算法真相。
- Pentest 旧 `LLMMCTSAdvisor` 会在 rollout 中真实调用只读工具；它退出生产 composition，不得作为公共 L3 的实现证据。
- Pentest 生产 L3 还必须交付纯虚拟 `transition/reward` 世界模型：不得触网或调用工具，预测 trace 可重放，并先以 shadow prediction 对真实后续结果做校准；未满足前只启用公共 L1/L2。
- 状态：`COMPLETED`。Pentest 已接入纯数据 deterministic virtual world；L3 默认 shadow-only，至少 20 个真实 outcome 对账且 MAE/误差覆盖率通过后才允许 influence。

### GR-02：第二与第三领域收益证明

- Code 与 Research 各有冻结数据集、固定预算、旧 planner 对照组。
- 至少报告成功率、验证通过率、搜索成本、延迟、无效工具率与可复现 trace；没有正收益不得默认启用 L3。
- 当前证据级别固定为 `SIMULATION_SHADOW`，明确不声称生产收益；延迟采用冻结成本模型的 deterministic logical latency，真实 wall-clock 不进入 digest。
- 状态：`COMPLETED`（Code/Research 固定模拟基准）；生产启用仍受各领域真实 shadow 校准 Gate 约束。

### GR-03：原生多节点 Durable TaskGraph

- admission 原子写入完整 DAG；未知依赖和 cycle 必须阻断。
- 调度只领取依赖已 ACCEPTED 的节点；fan-out/fan-in、重启恢复、失败传播成立。
- retry/idempotency/budget/review 由 durable runtime 消费，不依赖内存字段。
- 状态：`COMPLETED`。原子 admission、fan-out/fan-in、cycle/未知依赖阻断、Attempt/Lease/Idempotency/Budget/Review 与重启恢复均由 canonical TaskStore 消费。

### GR-04：搜索路径编译与 revision replan

- selected path 一次编译为 frozen graph revision。
- worker/Executive 不得再规划。
- failure replan 读取失败证据并产生 `revision + 1`；旧 revision immutable，receipt 可回溯选择 trace。
- 状态：`COMPLETED`。生产 Plan 路线只接受 Governor 绑定的 failure-replan graph/revision 引用；失败证据只能由 TaskStore 派生，真实 SQLite 已验证同 graph/root 的 `revision + 1` admission。

### GR-05：Universal Governed Runtime 生产切流

- chat、skill、delegate、plan 均生成 HostCommand 并进入同一 application/coordinator。
- Delegate node 具备 node-bound child grant；Plan 不再由 Executive 独立 `asyncio.gather` 执行。
- `RuntimeTaskRegistry` 仅可作为 DurableTaskStore 的只读兼容 projection，随后删除。
- 状态：`COMPLETED_CANONICAL_ROUTES`。chat、skill、delegate、plan 已统一切流；Executive 对四条 canonical route fail closed。`RuntimeTaskRegistry` 只剩非 canonical legacy compatibility 写入，所有生产状态读取已切换 durable projection。

### GR-06：事务副作用与最终验收

- user/assistant turn、Memory、Life、Delivery 由 Acceptance Outbox 绑定，admission/verification 失败不得产生已接受副作用。
- 小工作片只跑定向 unit/contract；每个 GR 波一次 integration；全部完成后统一启动前端、后端、Docker 和真实模型链路。
- 四条路线任何一条缺少 graph/attempt/verification/acceptance/receipt，最终状态必须为 `INCOMPLETE`。
- 状态：`LIVE_ROUTE_MATRIX_PASS`。Graph completion 与 Acceptance Effect intent 原子提交；user turn、Life、assistant/Memory projection 与 Gateway delivery 均具 durable retry/receipt，旧 Gateway 双发路径已切断。2026-07-15 已完成 Docker、Host、Observability、Monitor UI、真实 provider 与 chat/skill/delegate/plan 四路线实测。Edge API 完成安全切流，但本机未启用 ASR/TTS 且 VRM/VTS 未连接，因此只记为 `SAFE_CUT / DEGRADED`，不冒充完整具身能力通过。

## 4.1 当前机器验收事实

- GR-01～GR-06 代码聚合波：`213 passed`（2026-07-14）；最终后端选定回归波：`203 passed`（2026-07-15），其后 operation collision 清理路径定向回归 `5 passed`，CLI 持久化绑定与启动/迁移合同 `31 passed`。
- Pentest virtual L3 聚焦/回归：`93 passed`；零工具调用、确定性、预算、shadow parity、校准切流与真实 outcome 对账均覆盖。Code/Research 冻结基准共 10 例：greedy success/verification=`0.2/0.2`、invalid-tool=`0.4`，三层搜索为 `1.0/1.0/0.0`，其中 8/10 触发 Conditional MCTS，逻辑延迟成本增加 1097ms。证据等级仍为 `SIMULATION_SHADOW`，不得外推为生产收益。
- 前端最终验收：lint、TypeScript、production build 全部通过；operator client `7/7`、read model `12/12`、Node unit `29/29`、Playwright canonical suite `84/84`。
- Docker/进程探针：Redis `PONG`、Chroma heartbeat `200`、Neo4j HTTP `200`；Host `/health`=`200/UP`、Observability `/health`=`200`、Monitor UI=`200`。fresh migration head=`88`，SQLite integrity=`ok`、foreign-key violations=`0`。
- 四路线真实 provider + durable runtime：chat、skill、delegate、plan 均生成独立 request/graph lineage 并到达 `COMPLETED`；全部节点 `ACCEPTED`、attempt `SUCCEEDED`、lease `RELEASED`、budget `CONSUMED` 且无超限，Acceptance Effects 均 `COMMITTED`，Task Outbox unpublished=`0`。Plan 为 delegate→chat 两节点 immutable DAG，两个节点分别形成 durable attempt/idempotency/projection。
- 路线证据标识：chat `req_8059ec83b77b4936b7398cc5d4cb9cbc` / `graph-host-3705adea8b4d5ab027349f5a4ba303cb3990bf74`；skill `req_10d82a379f19456c80d2976c96497868` / `graph-host-6e0c8e0efe576bd0c76ea3d089a73456502691d6`；delegate `req_f3ef72e3728446fe8ace48374d1d3444` / `graph-host-d0793fc4ee22169d4c8ea687bfdd38db17b2b434`；plan `req_91610f0e505049edb7c5fea55707b5e1` / `graph-planning-f5d1a3fad2cdfaf8a3d8723050a76a8b5bac9f4b`。结构化、无正文证据见 `docs/45_testing/test_reports/gr06_universal_governed_runtime_live_acceptance_20260715.json`。
- 预算只消费真实测量：时间与 dispatch 必计；Provider 明确返回的 token/cost、ModelAttempt retry、可见 Agent tool steps进入 ledger。Provider 未返回的 token/cost 不估算，因此该维度保留 `UNMEASURED` 限制，不得伪称精确成本。
- `configs/release/universal_governed_runtime_coverage.json` 的四条路线控制均已为 `PASS`，但 `composition_without_global_service_locator` 仍为 `MISSING`；因此 `Full Architecture` 总状态仍保持 `INCOMPLETE`，不会复用本轮完成状态虚假封板。

## 5. 反伪绿规则

1. `PASS` 必须绑定生产 composition 和调用路径，未接线代码只能记为 `AVAILABLE_NOT_WIRED`。
2. 合同存在不等于 runtime 消费；projection 存在不等于 writer 已收敛。
3. direct skill 通过不能代表 chat/delegate/plan 通过。
4. cycle 不得通过忽略依赖、改成串行或 fallback 到 legacy 执行。
5. 测试不得把 mock 中不存在的 Budget、Review、Lease 或 grant 行为当作生产证据。
6. Full Architecture 只能由全路线 Gate 产生，Core Infrastructure 回执不得复用该名称。

## 6. 测试节奏

- 工作片：相关 unit/contract/static，目标分钟级。
- 波集成：仅在该波代码接线完成时运行一次。
- 本轮最终已执行：聚合后端回归、迁移完整性、前端 lint/typecheck/unit/build/Playwright、Docker 依赖、Host/Obs/UI 健康探针、四路线真实 provider 与 durable ledger 对账。
- 未纳入本轮 `PASS`：全仓所有历史测试、真实硬件 ASR/TTS/VRM/VTS、全局 service locator 删除。前两项分别记录为范围限制和 Edge `DEGRADED`；后一项使 `Full Architecture` 继续为 `INCOMPLETE`。

任何外部环境缺失只能留下最终实测欠项，不能阻止本地可继续的代码切流。
