受众：CODEX
标题：NLE WP-00 / TASK-0461 验证报告模板

# NLE WP-00 / TASK-0461 验证报告模板

版本：v1.0
日期：YYYY-MM-DD
任务：`TASK-0461 / LTE-WP-00`
基线提交：`<commit>`
实现提交：`<commit>`

## A. 变更摘要

1. `<行为变化>`

## B. 文件清单

| 文件 | 动作 | 所有权/说明 |
|---|---|---|
| `<path>` | ADD/MODIFY | TASK-0461 |

确认：用户业务 WIP、Pentest 运行 Canon、ignored DB/model/secret 均未进入 staged diff。

## C. 测试与证据

| 测试/门禁 | T 编号 | 结果 | 关键断言/证据 |
|---|---|---|---|
| 定向 Runtime | T-NLE-01 | `<result>` | 34 项基线不回退 |
| 边界扫描 | T-NLE-01 | `<result>` | 新违规拒绝、精确债务、stale allowlist |
| Collection | T-NLE-01 | `<result>` | 0 collection errors |
| Static/doc/delivery | T-NLE-01 | `<result>` | 0 violations / audience+freshness 通过 |

## D. 风险与回滚

- 风险：`<risk>`
- 停止条件检查：`<none or details>`
- 回滚：仅回退 TASK-0461 新增扫描、测试及工具修复；不得改动受保护 WIP。
- 未解决债务及后续 TASK：`<list>`

## E. MASTER 确认

- [ ] Baseline manifest 已确认。
- [ ] TASK-0461 验收通过。
- [ ] 允许解锁 TASK-0462 / TASK-0463 / TASK-0466。
