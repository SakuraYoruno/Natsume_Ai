受众：MASTER
标题：TASK-0486 M48-A Release Seal 验证报告

# TASK-0486 M48-A Release Seal 验证报告

版本：v1.0
日期：2026-07-11
任务：`TASK-0486 / LTE-WP-80`
测试：`T-NLE-24`
结论：`BLOCKED`

## 1. 结论先行

M48-A 的代码、离线迁移、restart/resume、SSE replay、degradation、canary/drain/kill switch、read model、Monitor build 与定点 E2E 已完成验证。Release Seal 不能进入 `READY_FOR_MASTER_SEAL`：缺少受信 previous IdentitySnapshot、Redis/Host/Obs/Neo4j/Chroma/Docker live 环境和获批生产 rollback 目标。该结论是硬 Gate，不是代码测试失败的隐式忽略。

## 2. 分层验证策略

按本轮正式执行口径：小任务只跑相关单测；跨模块大任务跑集成测试；里程碑波次只跑一次；波次发现的问题按失败闭集定点修复，不重复全量。

| 层级 | 结果 |
|---|---|
| Release Seal unit/contract | 17 passed |
| M48-A offline full-chain integration | passed；覆盖 SQLite migration clone/idempotence、event replay/SSE、degradation/review resume、canary/drain/kill switch、read models 与 flag rollback |
| Stable contract/integration remainder | 402 passed、2 Redis skips |
| Stable segmented unit | 所有闭集最终通过；相关 JUnit 保存在 `artifacts/release/m48/evidence/` |
| Monitor lint/typecheck/build | passed；Vite 2761 modules，生产 bundle 已提交 |
| Playwright milestone wave | 96 项：93 passed、3 failed；3 个 Pentest gate fixture/readonly 回归定点修复后 3/3 passed |
| Monitor post-boundary targeted E2E | Tools passed；Security Immune 断言按 model-owned gate cards 更新后 passed |

## 3. 唯一一次统一 Python 实测

命令：

```powershell
python -u -m pytest -vv --color=no --tb=short --durations=50 `
  -o faulthandler_timeout=300 `
  --junitxml=artifacts/release/m48/evidence/pytest-full.xml
```

结果：收集 7,439 个 pytest items；7,366 passed、71 failed、1 error、2 skipped，另有 15 subtests passed；总耗时 2,137.83 秒。npm 被显式置为 offline，避免测试通过 `npx -y` 隐式安装外部包。

72 个 failure/error 的闭集与处理：

| 闭集 | 原始数量 | 根因 | 定点结果 |
|---|---:|---|---|
| Neo4j/Chroma prod-env | 8 | 无 `.env`、Neo4j auth ref 或 Chroma listener | 4 passed、8 explicit skips；缺失依赖不再伪装成代码失败 |
| Bond Phase-V | 12 | unit fixture 错误连接 persistent Chroma，teardown 再次触发真实连接 | FakeChroma + SQLite `:memory:`；33 passed |
| Monitor API boundaries | 30 | 29 个断言仍指向拆分前 owner；1 个 execution/security 重名 fetcher | 真实 owner 断言 + 独立 fetcher；124 passed |
| Pentest autonomous-redteam | 22 | 固定旧 MCTS action 序列与当前单一 AgentLoop/fallback 不一致 | 保留 manifest/approval/evidence/redaction/report 不变量；107 passed + 2 精确修复后 passed |

没有执行第二次全量测试。原始失败 JUnit/log 与定点闭环证据同时保留。

## 4. 环境探测

| 依赖 | 结果 |
|---|---|
| Host Main `18080` | BLOCKED：未监听 |
| Observability `18082` | BLOCKED：未监听 |
| Redis `6379` | BLOCKED：未监听，`NASTUME_TEST_REDIS_URL` 未设置 |
| Chroma `8000` | BLOCKED：未监听 |
| Neo4j `7687` | BLOCKED：未监听且 auth ref 缺失 |
| Docker | BLOCKED：CLI 存在，daemon 未运行 |
| Kali/Nmap | BLOCKED：无 Kali distro；Nmap 不存在 |

## 5. 回滚与 identity

离线回滚矩阵中 8 项为 PASS；`identity_snapshot` 与 `production_migration` 为 BLOCKED。`unavailable://identity/...` 是显式哨兵，不是伪造 release snapshot。详细矩阵见 `docs/50_release/m48/M48_ROLLBACK_MATRIX.md`。

## 6. 发布状态

- TASK-0486：`BLOCKED`，说明限定为 `BLOCKED_AT_MASTER_SEAL`。
- M48-A：未 `SEALED`。
- M48-B Entry Gate：未批准。
- TASK-0487～0622：继续 `PROGRAMMED`，不得越级实施。

机器可读结果由 `tools/run_m48_release_seal.py` 生成；runner 预期退出码为 1、状态为 `BLOCKED`。
