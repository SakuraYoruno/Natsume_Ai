受众：MASTER
标题：质量、验收与历史修订目录

# 质量、验收与历史修订目录

版本：v2.0
维护日期：2026-07-15

本目录保存质量策略、专项验收方法和历史整改记录。它不是当前架构事实源：系统现状以
[CURRENT_CODE_ARCHITECTURE](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md)、
[SYSTEM_ARCHITECTURE](../50_project/arch/SYSTEM_ARCHITECTURE.md) 和
[ARCHITECTURE_GAP_MATRIX](../50_project/arch/ARCHITECTURE_GAP_MATRIX.md) 为准；当前实施范围以
[NEXT_GENERATION_EVOLUTION_EXECUTION](../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md) 为准。

## 当前质量入口

| 主题 | 入口 | 用途 |
|---|---|---|
| 架构策略门禁 | [`tools/static_scan/run_all.py`](../../tools/static_scan/run_all.py) | 检查分层、依赖方向、写入权威和已登记架构策略 |
| 文档权威门禁 | [`tools/doc_lint/doc_authority_gate.py`](../../tools/doc_lint/doc_authority_gate.py) | 防止权威入口缺失、重复或旧文档回流 |
| 文档新鲜度门禁 | [`tools/doc_lint/doc_freshness_gate.py`](../../tools/doc_lint/doc_freshness_gate.py) | 在代码源变化后要求相关架构文档同步更新 |
| 文档受众门禁 | [`tools/doc_lint/doc_audience_lint.py`](../../tools/doc_lint/doc_audience_lint.py) | 校验文档受众、标题和目录治理信息 |
| 需求追溯 | [requirements_trace](../60_trace/requirements_trace.md) | 连接需求、合同、代码、测试和实施波次 |
| 在线验收方法 | [LIVE_ACCEPTANCE_VALIDATION](LIVE_ACCEPTANCE_VALIDATION.md) | 记录真实服务验收范围和证据要求 |
| 独立生成回归 | [ISOLATED_SURFACE_GENERATION_HARNESS](ISOLATED_SURFACE_GENERATION_HARNESS.md) | 定义文本表面层隔离回归方法 |

## 专项质量资料

- [TEXT_STYLE_STANDARD_GALGAME](TEXT_STYLE_STANDARD_GALGAME.md)：文本风格约束。
- [SAMPLE_GUIDED_TEXT_QUALITY](SAMPLE_GUIDED_TEXT_QUALITY.md)：样本引导的文本质量方法。
- [AGENT_RUNTIME_SHARED_BREAKDOWN](AGENT_RUNTIME_SHARED_BREAKDOWN.md)：Agent Runtime 历史拆分分析；结论须与当前架构事实复核。
- [ORGAN_TRIBE_UPGRADE_PLAN](ORGAN_TRIBE_UPGRADE_PLAN.md)：Organ/Tribe 历史升级方案；不代表当前生产完成度。

## 历史整改记录

以下文件保留的是某一时间点的方案、审计或基线，不得作为“当前已完成”的证据：

- [ARCH_FIX_PLAN](ARCH_FIX_PLAN.md)
- [OPTIMIZATION_PLAN_20260420](OPTIMIZATION_PLAN_20260420.md)
- [SELF_BUILT_MECHANISM_FIX_PLAN](SELF_BUILT_MECHANISM_FIX_PLAN.md)
- [audit_report_20260311](audits/audit_report_20260311.md)
- [`audits/`](audits/index.md)

旧 Gateway 重设计草稿、旧数字生命完成度审计和已被当前权威文档取代的质量报告已从工作树删除；需要追溯时使用 Git 历史。

## 使用规则

1. 架构判断先查当前事实、差距矩阵和机器状态，不从历史整改计划反推现状。
2. 声称功能完成必须同时给出生产接线、测试和真实运行证据。
3. 单测只证明局部合同；跨进程数据流、Docker、外部服务和 UI 需要对应的集成或实测证据。
4. 历史文档中的路径、数量、状态和百分比默认可能过期，引用前必须重新核验。
5. 新增长期有效的质量约束时，优先固化为门禁或测试，而不是再创建一份一次性报告。
