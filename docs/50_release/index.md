受众：MASTER
标题：固定发布证据目录

# 固定发布证据目录

本目录主要是工具生成且被代码、配置或测试引用的历史发布证据，不是当前文档入口。

- `core/`：Core Release manifest、SBOM、prepare/acceptance receipts 和多次尝试记录；不得删除或改写。
- `m48/`：M48 固定 evidence、manifest、identity、rollback 和 seal 结果。
- `changelog/`：已发布变更日志。
- `rollback/`：历史回滚 runbook 与复核记录。

2026 年 3 月的封板计划和执行报告已归档到 `docs/_history/releases/2026-03/`。当前状态见 [机器状态](../../configs/release/current_architecture_status.json)，不能从本目录中的旧 `PASS` 推导。

部分早期 receipt 或 release manifest 仍会指向当时未提交仓库的本地日志、评审报告或运行产物。这些缺口属于历史证据本身，保留原始引用，不补造文件，也不将其计入当前文档链接 Gate。
