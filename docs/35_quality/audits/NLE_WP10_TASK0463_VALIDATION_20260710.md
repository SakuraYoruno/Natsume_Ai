受众：CODEX
标题：NLE WP-10 / TASK-0463 Pentest Domain Store 验证报告

# NLE WP-10 / TASK-0463 Pentest Domain Store 验证报告

版本：v1.0
日期：2026-07-10
状态：`PASSED`
任务：`TASK-0463 / LTE-WP-10`
基线提交：`030ad1f98`
实现提交：本报告所在 `TASK-0463` 单任务提交

## A. 行为变化

1. 新增 Pentest 自有 SQLite Domain Store 和标准化 `PentestKnowledgeEntry`，覆盖 successful chain、pitfall、reflection、action outcome 与 evidence refs。
2. canonical source 序列化与 SHA-256 稳定；payload hash、schema version 或数据库内容漂移时 fail closed。
3. 新增只读 shadow adapter；只观察 Canon 读取并对比 Domain Store，不替换 active 返回、不调用写接口。
4. 新增中央 `0050_pentest_knowledge.sql` expand-only migration 与下一阶段回填 manifest schema；未创建第二 migration catalog。
5. 隔离既有知识单测的真实 Canon 写副作用；需要持久化的用例继续显式注入 mock。

## B. 文件与所有权

| 文件 | 动作 | 所有权/说明 |
|---|---|---|
| `host_brain/pentest/knowledge_store.py` | ADD | Pentest Domain Store、entry contract、shadow observer |
| `host_brain/audit/migrations/0050_pentest_knowledge.sql` | ADD | central audit migration catalog；expand-only |
| `host_brain/pentest/schemas/pentest_knowledge_migration_manifest.schema.json` | ADD | TASK-0464 回填/对账证据合同 |
| `host_brain/pentest/agent/knowledge.py` | MODIFY | 可选、默认关闭、只读 shadow observer |
| `tests/contract/test_pentest_knowledge_store_contract.py` | ADD | T-NLE-02 |
| `tests/unit/test_pentest_knowledge_online_learn.py` | MODIFY | 禁止单测写入源码 Canon |

确认：未删除或修改现有 Canon 文档，未切换 active reader/writer，未修改 Pentest 执行策略，未开始 TASK-0464 回填。

## C. 测试证据

| 门禁 | 结果 | 关键断言 |
|---|---:|---|
| Store + Knowledge 定向测试 | 37 passed | 稳定序列化/hash、四类知识、重启、检索、幂等、shadow 不改返回 |
| Migration runner + NLE boundary | 7 passed | 中央资产可重复执行；现存边界未新增/失效 |
| 合计 | 44 passed | 0 failed |
| Static scan | passed | 0 violations |
| Canon 工作树检查 | clean | 连续复跑不生成源码 Canon 文件 |
| JSON/compile/diff | passed | schema 可解析；无 whitespace error |

## D. 风险、停止条件与回滚

- 已知风险：central audit catalog 存在历史重复 `0009` 与 `0023～0038` 缺口；本任务不得扩围修复。Store 只 bootstrap 同一个 `0050` 资产，不建立平行 catalog。
- 停止条件检查：四类知识与 evidence refs 均可无损表达，未命中 schema 不足停止条件。
- Shadow 风险：默认关闭、只读、异常被隔离；其结果不能改变 active Canon 返回。
- 回滚：移除 Store、schema、0050 migration 与可选 observer；数据库表保持 expand-only，不破坏已写数据。
- 后续：TASK-0464 负责可重复 backfill、shadow 对账、final delta 与 domain-only 切换；本任务不授权提前切流。

## E. 自动推进裁决

- [x] T-NLE-02 与边界门禁通过。
- [x] TASK-0463 可标记 DONE 并按单任务提交。
- [x] TASK-0464 依赖 Gate 已解锁；按完整重构 Goal 自动推进。
