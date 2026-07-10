受众：CODEX
标题：NLE WP-00 / TASK-0461 验证报告

# NLE WP-00 / TASK-0461 验证报告

版本：v1.0
日期：2026-07-10
状态：`PASSED`
任务：`TASK-0461 / LTE-WP-00`
代码基线：`1ec8be1a8a20397627855aa946c0ff8aaaa87b72`
程序文档基线：`760842ab6da88a4134a94601c75b0da32416cb79`
实现分支：`codex/m48-task0461`
实现提交：本报告所在 `TASK-0461` 单任务提交

## A. 变更摘要

1. 对原 dirty 工作树的 26 个 Git-visible 非文档改动逐文件分类、hash 并制作仓库外归档；TASK-0461 在独立 linked worktree 实施。
2. 新增 5 类 NLE 高风险边界规则，冻结 21 条精确 `rule_id + path + symbol` 现存债务；新增依赖、stale allowlist 和 glob allowlist 均失败。
3. 文档 pre-commit hook 改为真实 audience CLI，并只检查 staged Markdown。
4. Freshness 对 modified、staged、untracked 文档使用 filesystem mtime，对 clean tracked 文档保留 Git timestamp；未放宽 `max_age_days`。
5. 修复 `candidate_scoring.py:1097` 的 invalid escape；另外三个同型历史副本保持登记债务，不在本任务扩围修改。

## B. 仓库保护证据

| 项目 | 结果 |
|---|---|
| 原工作树 | `D:/Natsume_Ai`，26 个 Git-visible dirty 文件原位保留 |
| 用户业务 WIP | 15 个文件，全部分类为 `protected_user_business_wip` |
| Pentest 运行 Canon | 11 个文件，全部分类为 `generated_pentest_canon_data` |
| Unclassified | 0 |
| 外部归档 | `D:/Natsume_Ai_backups/pre_task_0461_visible_worktree_20260710_153527.tar.gz` |
| 归档 SHA-256 | `35083D59FA9451D65B9B29A038789787D35128EC7630BB32FD875CD9E574A894` |
| 实施工作树 | `D:/Natsume_Ai_task0461` |

该归档只覆盖 Git-visible 变更；ignored DB、model、secret、cache 和运行证据未被读取、移动或清理。

## C. 测试与门禁

| 测试/门禁 | T 编号 | 结果 | 关键证据 |
|---|---|---:|---|
| TASK-0461 新增单元/契约测试 | T-NLE-01 | 21 passed | inventory 4、freshness 7、repo hygiene 5、boundary 5 |
| 定向 Runtime | T-NLE-01 | 34 passed | 冻结基线不回退 |
| 既有架构边界 | T-NLE-01 | 14 passed | 冻结基线不回退 |
| NLE boundary scan | T-NLE-01 | 0 violations | 21 条 exact existing debt；stale/new/glob 负例通过 |
| 聚合 static scan | T-NLE-01 | passed | Bond、no-rule-degrade、NLE 均为 0 violations |
| 全量 pytest collection | T-NLE-01 | 6893 collected | 0 collection errors |
| 稳定范围 collection | T-NLE-01 | 6461 collected | 383 deselected；0 collection errors |
| Pre-commit audience hook | T-NLE-01 | passed | staged Markdown 转换为重复 `--path` |
| Audience / freshness / delivery | T-NLE-01 | passed | 见本报告封板复跑 |
| `git diff --check` | T-NLE-01 | passed | 无 whitespace error |

收集数相对 dirty 基线 `6881 / 6449` 均净增 12：TASK-0461 新增 21 个测试，独立工作树未携带受保护用户 WIP 中的 9 个新测试，故 `+21 - 9 = +12`。原工作树两份受保护测试收集为 22，干净基线对应文件收集为 13；不存在通过删除测试维持绿色的情况。

## D. 边界债务与停止条件

| 规则 | 精确债务数 | 后续移除 owner/task |
|---|---:|---|
| Edge → Host implementation import | 3 | EDGE_RUNTIME / M55_EDGE_RUNTIME |
| AgentRuntime → App module import | 1 | AGENT_RUNTIME / M50_AGENT_RUNTIME |
| Pentest → WorldCanon import | 9 | PENTEST_DOMAIN / TASK-0463 |
| Pentest direct `upsert_document` | 8 | PENTEST_DOMAIN / TASK-0463 |
| Parallel `host_brain/life` root | 0 | 禁止新增 |

Allowlist 总计 21 条，但每条均逐文件逐符号登记、无 glob，单规则最大 9 条；删除任一条都会制造 stale/未登记事实。因此未命中“必须依赖大范围 allowlist 才能通过”的停止条件。

## E. 风险、回滚与待确认

- 已知风险：扫描器冻结的是结构化 Python import/call 与目录边界，不替代运行时授权测试；同一文件同一符号的多行调用聚合为一条债务并完整报告行号。
- 历史警告：三个同型 persona scoring 副本及其它既有 invalid escape 仍会产生 warning，未在 TASK-0461 扩围修复。
- 回滚：仅回退 TASK-0461 新增扫描、测试及工具修复；不得改动受保护 WIP 或 Pentest 运行 Canon。
- 后续任务：确认后解锁 TASK-0462、TASK-0463、TASK-0466；TASK-0463 负责收缩 Pentest Canon 边界债务。

## F. MASTER 确认

- [x] Baseline manifest 的 15 个业务 WIP、11 个 Pentest Canon 与外部归档 hash 已确认。
- [x] 21 条精确架构例外清单已确认。
- [x] TASK-0461 可标记 DONE 并按单任务提交。
- [x] 允许解锁 TASK-0462 / TASK-0463 / TASK-0466。
