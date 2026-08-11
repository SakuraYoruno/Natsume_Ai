受众：PROJECT
标题：代码与文档同步规则

# 代码与文档同步规则

## 唯一职责

| 文档 | 职责 |
|---|---|
| README | 产品入口、启动、现状和限制 |
| Theory／Whitepaper | 长期原则与人格边界 |
| PRD | 用户体验、需求和指标 |
| DevSpec | 合同、运行边界和迁移 |
| Current Architecture | 当前代码事实 |
| Target Architecture | 最终目标 |
| Gap Matrix／DevTasks | 剩余缺口与任务 |
| TESTPLAN／Trace | 测试方法和需求证据 |
| machine status | 当前可机读状态 |

## 触发规则

- Life、关系、表达变化：Theory、Current、Data Flow、图 05、Trace。
- Surface/model 变化：Model Plane、Current、主链图、测试计划。
- Task/effect 变化：Current、Data Flow、图 02/04、Trace。
- Gateway/Edge/设备变化：Deployment、图 01/06、对应指南。
- OBS/Monitor 变化：Monitor、图 07、Functional Modules。
- 状态变化：machine status、Gap、Execution、DevTasks。

只更新受影响 owner 文档，不在每份文档重复一遍提交历史。

## 归档

- 当前 authority 只保留仍指导产品或代码的文档。
- 被取代的计划、审计和固定报告移入 `docs/_history/`。
- 工具或测试硬引用的机器 evidence 保留原路径，并在索引中标记 scope-bound。
- migration、审计证据和发布 receipt 不因“整理”删除。
- Git 历史足以保存没有长期证据价值的文字草稿。

## 门禁

```powershell
python tools/doc_lint/doc_audience_lint.py --root .
python tools/doc_lint/doc_authority_gate.py --root .
python tools/doc_lint/doc_freshness_gate.py --root . --check-all
python tools/doc_lint/doc_link_gate.py --root .
```

提交前还需检查内部链接、`git diff --check` 和受影响测试。历史 `PASS` 不得覆盖当前机器状态。
