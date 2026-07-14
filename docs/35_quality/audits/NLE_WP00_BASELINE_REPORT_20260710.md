受众：PROJECT
标题：NLE WP-00 / TASK-0461 基线审计报告

# NLE WP-00 / TASK-0461 基线审计报告

版本：v1.0
日期：2026-07-10
状态：`DONE`
任务：`TASK-0461 / LTE-WP-00`
运行时代码基线：`dev@1ec8be1a8a20397627855aa946c0ff8aaaa87b72`
程序文档基线：`760842ab6da88a4134a94601c75b0da32416cb79`
实施分支：`codex/m48-task0461`
实施工作树：`D:/Natsume_Ai_task0461`

## 1. 仓库整理与所有权

原工作树 `D:/Natsume_Ai` 保留用户业务开发与 Pentest 运行数据；TASK-0461 在独立 linked worktree 中实施。禁止在原工作树执行 `git add -A`、广泛 stash、reset、clean 或 worktree prune。

| 分类 | 数量 | 处置 |
|---|---:|---|
| 架构程序文档 | 15 | 已独立提交为 `760842ab` |
| 受保护的日文媒体翻译 WIP | 15 | 原位保留，不属于 TASK-0461 |
| Pentest 生成 Canon 数据 | 11 | 原位保留，等待 Domain Store/Canon promotion 任务处理 |
| TASK-0461 干净工作树 | 1 | 从 `760842ab` 创建，独立分支与精确暂存 |

可见的 26 个非文档 dirty 文件已另存仓库外归档：

- 路径：`D:/Natsume_Ai_backups/pre_task_0461_visible_worktree_20260710_153527.tar.gz`
- SHA-256：`35083D59FA9451D65B9B29A038789787D35128EC7630BB32FD875CD9E574A894`
- 归档条目：27（包含浏览器扩展目录项）

该归档不包含 ignored 数据库、模型、secret、缓存和运行证据；本任务不清理这些路径。

## 2. 当前调用路径清单

| 分类 | 路径/符号 | 代码事实 | 后续任务 |
|---|---|---|---|
| ACTIVE | `chat_handler → cortex_orchestrator → agent_runtime_shared → executive_runtime_core` | 当前生产聊天/复杂任务主链 | TASK-0466～0473 |
| ACTIVE | `executive_runtime_core.py` 的 PlanningAgent 路径 | 普通复杂任务仍会二次规划 | TASK-0468 |
| COMPATIBILITY | `GraphRuntimeContract` | 主要用于装配和 startup warmup，不是生产聊天 authority | TASK-0466～0473 |
| ACTIVE_BROKEN | `pentest/campaign_memory.py` 调用链 | 有真实调用者，但签名漂移后吞异常 | TASK-0463～0464 |
| ACTIVE_UNGOVERNED | Pentest `WorldCanonStore.upsert_document()` 调用 | 多条生产路径可直接写 Canon | TASK-0463～0465 |
| ACTIVE_SENSITIVE | Agent ReAct `thought` / scratchpad | 会进入 durable/propagated 结构 | TASK-0462 |
| DEAD_CANDIDATE | `executive_runtime_core.py:1453+` 旧 SkillDag 分支 | 正常路径不可达，暂不物理删除 | TASK-0473 / TASK-0620 |
| DEAD_CANDIDATE | `pentest/swarm/swarm_kernel.py::run_swarm` | 未发现生产调用者 | TASK-0563 / TASK-0620 |

`DEAD_CANDIDATE` 只表示当前引用审计未发现生产入口，不授权本任务删除代码。

## 3. 已冻结测试基线

| 基线 | 结果 |
|---|---:|
| 定向 Runtime 测试 | 34 passed |
| 架构边界测试 | 14 passed |
| 全量 pytest collection | 6881 collected，0 collection errors |
| 稳定范围 collection | 6449 collected，383 deselected，0 collection errors |
| 既有 static scan | 0 violations |
| Monitor UI Playwright discovery | 93 tests |

数量变化必须在任务证据中解释，禁止通过删除测试维持绿色。

## 4. TASK-0461 基线缺口

以下是基线时点发现的问题；处置结果见 `NLE_WP00_VALIDATION_REPORT_20260710.md`。

1. `.pre-commit-config.yaml` 指向不存在的文档 lint 入口，且参数不符合真实 CLI。
2. `candidate_scoring.py:1097` 含 invalid escape；另有三个同型副本未获本任务修改授权。
3. Freshness 对 dirty/staged tracked 文档仍优先采用 Git commit timestamp。
4. Static scan 只有 Bond SSOT 与 no-rule-degrade，缺少长期演化高风险依赖边界。
5. Monitor UI hashed 构建资产仍被跟踪；Pentest 运行知识仍写入源码 Canon 目录。两项均登记但不在本任务删除。

## 5. 停止条件

- 新边界规则必须依赖目录级或 glob 级宽泛 allowlist 才能通过。
- 无法把原工作树用户 WIP、运行数据与 TASK-0461 变更区分。
- 扫描器要求修改运行时逻辑、schema、主链或 Canon 数据才能绿色。
- Freshness 修复需要放宽 `max_age_days` 或忽略 dirty 文档。

命中任一项时，TASK-0461 保持 `BLOCKED`，不得启动 TASK-0462/0463/0466。

## 6. 待封板证据

- [x] 原工作树保护清单与外部备份 hash 可核验。
- [x] NLE allowlist 精确到 rule/path/symbol，并能检测 stale 条目。
- [x] Pre-commit、warning、freshness 和 static scan 修复具自动化测试。
- [x] 34 定向、14 边界、collection、static/doc/delivery gates 复跑通过。
- [x] 验证报告按模板完成。
- [x] MASTER 确认 baseline manifest 与 21 条精确架构例外。
- [x] TASK-0461 标记 DONE 并按单任务提交。
