受众：PROJECT
标题：Natsume AI 文档与代码同步工作流

# Natsume AI 文档与代码同步工作流

版本：v2.1
维护日期：2026-07-15

文档不是代码改完后的装饰。当前架构、模块边界、数据流和机器状态必须随 production composition 同步；历史报告只保存其固定范围，不允许反向覆盖当前事实。

## 1. 文档职责

| 文档 | 只负责什么 | 不允许写什么 |
|---|---|---|
| `README.md` | 项目入口、全景、启动、验证和导航 | 细粒度任务状态 |
| `docs/15_theory/Theory.md` | 长期不变量和概念关系 | 当前完成度和排期 |
| `SYSTEM_ARCHITECTURE.md` | 当前结构、分层、进程和 authority | 大量易腐实现枚举 |
| `FUNCTIONAL_MODULES.md` | 功能 → owner → 入口 → 依赖 → 缺口 | 历史里程碑叙事 |
| `DATA_FLOW_ARCHITECTURE.md` | 当前数据、状态、副作用和 receipt | 把目标箭头画成当前实线 |
| `CURRENT_CODE_ARCHITECTURE.md` | 代码现在是什么 | 未来承诺 |
| `TARGET_ARCHITECTURE.md` | 稳定最终目标 | 当前状态和任务进度 |
| `ARCHITECTURE_GAP_MATRIX.md` | 当前与目标的差距 | 新增独立目标 |
| `NEXT_GENERATION_EVOLUTION_EXECUTION.md` | 唯一活动波次、依赖、Gate | 重复产品/理论全文 |
| machine status JSON | 自动化消费的当前定级 | 解释性长文 |

## 2. 变更触发矩阵

| 改动范围 | 必须检查/更新 |
|---|---|
| ASGI、进程、composition、package owner | README、System、Functional Modules、Current |
| Secure Ingress、Governor、route | Data Flow、图 02、Current、Gap、Trace |
| TaskGraph、Attempt、Lease、Budget、Review、Acceptance | Data Flow、图 02/04、Current、Gap、TestPlan |
| PlanningSearch 或领域 adapter | Theory、System、Data Flow、图 04、Current、Gap |
| Life、Memory、Bond、Persona、Scene、World | Theory、Data Flow、图 03/05、Current、Gap |
| Gateway、Edge、跨进程合同 | Data Flow、图 01/06、Deployment、Current |
| Monitor route/query/command/API | Monitor UI、图 07、Functional Modules |
| 环境变量、启动脚本、Docker、端口 owner | README、Deployment、示例配置 |
| 当前状态或 Gate | machine status、Current、Gap、Execution、Trace |

没有事实变化时不需要机械修改所有文档；有事实变化时，不能只改一个版本日期或写“同步中”。

## 3. 更新步骤

1. 从生产入口和 composition 复现调用链，不从旧文档复制。
2. 判断变化属于理论、结构、当前状态、目标、差距还是执行，选择唯一 owner 文档。
3. 更新代码路径、状态定级和必要 Mermaid；目标关系使用虚线或明确标 `TARGET`。
4. 更新受影响索引和链接。
5. 若状态变化，同步 `configs/release/current_architecture_status.json`。
6. 若 FR 或 Gate 变化，同步 Trace/TestPlan/执行合同。
7. 运行文档门禁、链接检查、相关代码测试和静态扫描。

## 4. 自动门禁

```powershell
python tools/doc_lint/doc_audience_lint.py --root .
python tools/doc_lint/doc_authority_gate.py --root .
python tools/doc_lint/doc_freshness_gate.py --root . --check-all
python tools/delivery/change_metadata_gate.py --root . --base-ref origin/dev
python -m pre_commit run
```

- `doc_audience_lint`：检查受众/标题头和混写规则。
- `doc_authority_gate`：检查核心文档存在、旧执行计划隔离、机器状态与执行合同一致。
- `doc_freshness_gate`：按源码变更提醒事实文档过期。
- `change_metadata_gate`：检查 Feature 分支无 merge commit、中文规范提交和真实 Markdown PR 七章节；Ready 前追加 `--pr-body-file`、`--pr-title` 与 `--require-pr-metadata`。
- pre-commit：在提交前组合运行文档 Gate。

PR 正文必须先写入本地 Markdown 文件，再使用 `gh pr create/edit --body-file <path>`；不得把 `\\n` 拼进内联 shell 参数。GitHub Actions 因账户额度未分配步骤时记录 `NOT_RUN_ACCOUNT_QUOTA`，本地报告写入 `artifacts/local-gates/<commit-sha>/`，两者都不能冒充远程 PASS。

门禁通过只证明规则覆盖的内容自洽，不替代人工核对生产接线。新增重要文档时应把它加入 authority/freshness Gate，而不是只加一个链接。

## 5. 删除与归档

- 无当前价值的旧方案、重复完成报告和模块快照直接删除，Git 历史承担追溯。
- 只有发布回执、合规证据或需要长期保留的固定程序快照进入 `_history`/`50_release`。
- 历史文件必须有范围和基线，不能从当前目录被当作活动计划引用。
- 不为“文件必须存在”单独写测试；测试应验证权威链、状态一致性和链接有效性。

## 6. 完成定义

文档改动完成必须满足：

1. 生产代码路径真实存在。
2. 当前、目标和历史没有混写。
3. Mermaid 与正文使用同一状态口径。
4. 核心相对链接全部有效。
5. machine status 与 Current/Gap/Execution 一致。
6. 文档门禁和受影响测试通过。
7. README 与目录索引能让新成员找到新文档。
