受众：CODEX
标题：CI Workflows

`delivery-gates.yml` 是当前唯一交付门禁。

- PR 和 `main` push 默认运行稳定门禁：`tests/unit`、`tests/contract`、`tests/integration`，并排除 `e2e`、`hw`、`prod_env`、`runtime_probe`、`docker`、`ci_unstable` 等依赖外部环境或历史资产的 marker。
- `workflow_dispatch` 勾选 `run_full_suite` 或每日定时任务运行全量 pytest，用来观察长尾和环境相关问题，不作为普通 PR 的默认噪声源。
- Python 依赖来自 `requirements.txt`，测试工具链在 workflow 中显式安装；仓库不再依赖不存在的 `requirements-dev.txt`。
- 静态交付门禁继续运行 `static_scan`；`doc_gate` 只检查当前 CI 工作流说明，历史 Markdown 归档不再阻断 PR。
- CI 额外检查 Codex 硬切后旧链关键字不会回到运行代码、测试、脚本或 prompts。
