受众：PROJECT
标题：TASK-0480～TASK-0485 Capability、Degradation、Identity 与治理 UI 验证报告

# TASK-0480～TASK-0485 / LTE-WP-60～72 验证报告

版本：v1.0
日期：2026-07-11
状态：`PASSED / NOT_SEALED`
集成分支：`codex/m48-integration`
集成基线：`d43fb61c`

## 1. 结论

TASK-0480～TASK-0485 已按 T-NLE-18～23 完成。Capability Maturity 只收紧既有 Governor 决策；DegradationState 使用 durable transition/recovery evidence 并在高风险前门 fail-closed；Release Identity Snapshot 不复制私密人格或记忆正文，重大差异只形成 MASTER Review candidate；Execution、Life 与 Governance UI 均消费 versioned public read model，不新增数据库直写、自动升级、自动身份确认或 Pentest 非 Lab 执行旁路。

Governance Review 链已补齐真实性约束：candidate ref 不再冒充 ReviewStore ID；只有已 materialize 的 durable Review 才生成 `review_id` 链接。TaskRuntime 通过只读 locator 精确定位 Review，支持有/无 TaskGraph 两种绑定；schema、ID、graph、execution 或 Review 不一致时均关闭命令入口。Identity `UNAVAILABLE` 不再显示为“无需确认”，MAJOR diff 缺 durable Review 时 fail-closed。

本报告只判定 WP-60～72 的任务完成，不代表 M48-A 已封板。TASK-0486 的集中实测、迁移/故障/回滚演练、Seal Result 与 MASTER 最终批准仍是硬 Gate。

## 2. 提交与交付矩阵

| TASK | 集成提交 | 主要交付 |
|---|---|---|
| TASK-0480 | `cba753e9c`、`e1a0ac7b7` | federated scorecard、canonical risk、L0～L5、实际 registry provider、restrict-only Governor adapter |
| TASK-0481 | `24b2c3c5c`、`bed27bc93` | durable DegradationState、恢复 Review、跨高风险入口门禁 |
| TASK-0482 | `362dc43ba` | immutable Identity Snapshot、diff/review、previous-release rollback plan |
| TASK-0483 | `cfee20220` | versioned Execution/Review Operator UI；详见独立 WP-70 验证报告 |
| TASK-0484 | `02a5f27a6` | producer-backed Life evolution read views 与无执行 dry-run |
| TASK-0485 | `2968b7d15`、`f2b76586d`、`e31a406a0`、`caf0369d9`、`b8dcdfe8`、`d43fb61c` | governance snapshot、Skills/System Config/Security UI、Pentest isolation、durable Review locator 与真实深链 |

## 3. 验证证据

- T-NLE-18：`35 passed`。
- T-NLE-19：两个 focused 合同文件合计 `19 passed`。
- T-NLE-20：`11 passed`。
- T-NLE-22：Python `12 passed`；Life Node model `4 passed`；相关 T-NLE-17 `12 passed`。
- T-NLE-23：backend governance `8 passed`；governance Node model `6 passed`；Pentest isolation Node model `3 passed`。
- Review locator / TaskRuntime 深链：operator contract `9 passed`；TaskRuntime Node model `7 passed`；JSX parse 通过。
- 0485 最终 focused 合并验证：Python `17 passed`，Node `16 passed`，`git diff --check` 通过。
- 波次相关回归首次运行 `89/90 passed`，唯一失败来自 T-NLE-22 测试隐式读取 ignored runtime audit DB 的 migration/durability 状态；修复为显式隔离真实 degradation gate 后，T-NLE-22 `12/12 passed`。未重复运行其余已经通过的 89 项。
- 最终 Monitor Vite production build：`2761 modules transformed`，通过；静态资产已由该次预期 build 更新。

## 4. 独立审查阻塞项与关闭

| 阻塞项 | 关闭结果 |
|---|---|
| Maturity 可能把异常等级夹成 L5、缺实际 provider、evidence 可绕过 | 改为严格 schema/range、实际 registry provider、evidence 与 restrict-only composition |
| Degradation 只在 resource manager、生存期内存态、bool review 可绕过 | 接入 RuntimeEventStore durable restore、一致门禁与 subject/version/checkpoint 一次性 Review resume |
| Life preview 可由浏览器伪造 authority、空 Scene 被合成 ALLOW | authority 改为 server trusted token 非消费校验；空数据 `UNAVAILABLE`；preview 永不执行/记录 |
| Identity unavailable 被显示为无需确认；MAJOR diff 无 Review 仍 safe | unavailable 显示未知并 fail-closed；MAJOR 必须绑定 durable Review |
| Governance Review 链接指向 candidate ref，TaskRuntime 不消费 query | 新增 versioned review locator；链接改用真实 `review_id`；TaskRuntime exact-match 并支持 unbound Review |
| Security Lab 页面未消费 governance snapshot | 正常路由接入 governance query；404 回退既有 server environment，加载/错误态 fail-closed |

## 5. 回滚与停止条件

| 增量 | 回滚开关/动作 | 不变量 |
|---|---|---|
| Capability Maturity | `HOST_CAPABILITY_MATURITY_POLICY_ENABLED=false` | 保留静态风险、原 Governor 与降级记录 |
| DegradationState | `HOST_DEGRADATION_STATE_ENABLED=false` | 状态为 restricted disable；不得清除已锁存高风险限制 |
| Identity Snapshot | `HOST_RELEASE_IDENTITY_SNAPSHOT_ENABLED=false` | 不自动修改人格；保留 snapshot/diff evidence |
| Life projection/UI | `HOST_LIFE_PROJECTION_ENABLED=false`、`HOST_NLE_LIFE_VIEWS_ENABLED=false` | 不影响 Life backend；无新增 Life Core 写入 |
| Operator/Governance UI | `HOST_NLE_OPERATOR_VIEWS_ENABLED=false`、`HOST_NLE_GOVERNANCE_VIEWS_ENABLED=false` | 仅隐藏 read route/UI；后端 Review/Pentest 门禁继续生效 |
| Pentest | `PENTEST_WEB_LAB_MODE=false`、`PENTEST_ALLOW_EXPLOIT=false` | 非 Lab create/approve/continue 始终拒绝；不恢复 Canon 写旁路 |

未触发以下停止条件：成熟度放宽审批；降级时开放受影响高风险能力；Identity diff 泄漏私密正文；UI 直接写库或用普通 mutation/布尔确认绕过 Review；非 Lab 暴露可执行 Pentest 高风险入口。

## 6. 未封板边界

- M48-A 全量稳定测试、full report、Monitor E2E、migration clone dry-run、restart/resume、SSE reconnect、fault injection、canary/drain/kill-switch、rollback drill 与 live probes 统一由 TASK-0486 执行。
- 仓库当前没有受信 previous IdentitySnapshot；不得伪造。需恢复前版资产，或由 MASTER 明确批准 M48 建立首次 baseline。
- 最终自动状态最多为 `READY_FOR_MASTER_SEAL`；`SEALED` 只能由 MASTER 明确批准。
- M48-B Entry Gate 独立于 M48-A Seal，未获 MASTER 明确批准前不得开始 TASK-0487。
