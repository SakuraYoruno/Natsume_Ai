受众：PROJECT
标题：当前测试与历史证据入口

# 当前测试与历史证据入口

当前测试方法以 [TESTPLAN](../../tests/TESTPLAN.md) 为准。

| 内容 | 入口 | 状态语义 |
|---|---|---|
| 情感生命与全栈测试计划 | [TESTPLAN](../../tests/TESTPLAN.md) | 当前 |
| 系统 2.0 总验收合同 | [SYSTEM_V2_MASTER_ACCEPTANCE_PLAN](../35_quality/SYSTEM_V2_MASTER_ACCEPTANCE_PLAN.md) | 当前；覆盖架构、搜索收益、人感和最终放行 |
| 双机全栈详细执行方案 | [DIGITAL_LIFE_FULL_STACK_TEST_PLAN](../35_quality/DIGITAL_LIFE_FULL_STACK_TEST_PLAN.md) | 当前 |
| Governed Runtime 固定收据 | [test_reports](test_reports/index.md) | 历史、scope-bound |
| Pentest 验收说明 | [pentest](pentest.md) | 当前边界 |
| 脆弱靶场清单 | [pentest_vulnerable_labs](pentest_vulnerable_labs.md) | 本地授权测试参考 |
| 旧 Pentest 能力图 | [历史测试快照](../_history/testing/README.md) | 归档 |

测试报告、receipt 和静态合同只能证明其绑定 commit 与环境。当前全仓 line／branch coverage 尚未生成，pytest 仍有 1 个 collection error；Architecture 2.0 当前 SHA 总验收、PlanningSearch 三域真实收益、目标 Edge 完整三模态和至少 3 人真实多轮盲测均未完成。
