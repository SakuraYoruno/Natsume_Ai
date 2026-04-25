受众：CODEX
标题：CI Workflows

`delivery-gates.yml` 是默认交付门禁；服务依赖和现场探针拆到独立 workflow。

- PR 和 `main` push 默认运行稳定门禁：`tests/unit`、`tests/contract`、`tests/integration`，并排除 `e2e`、`hw`、`prod_env`、`runtime_probe`、`docker`、`ci_unstable` 等依赖外部环境或历史资产的 marker。
- `workflow_dispatch` 勾选 `run_full_suite` 或每日定时任务运行全量 pytest；该 job 是报告型，失败按服务依赖或现场探针归类，不作为普通 PR 的默认噪声源。
- `service-integration.yml` 手动/定时运行 Redis、Chroma、Neo4j 依赖测试，并临时生成 CI 专用 `.env` 和本地 secret store。
- `live-probes.yml` 只在带 `self-hosted`、`windows`、`natsume-live` 标签的 runner 上手动运行 `hw`、`runtime_probe`、`e2e`。
- `bio-ops-automation.yml` 手动/定时运行 BioOps sidecar 工作流：daily cycle → owner inbox → owner intake packet → owner queue artifact。默认不自动接受真实 candidate，不写长期状态；手动触发时可启用 demo owner decision 做链路冒烟。
- Python 依赖来自 `requirements.txt`，测试工具链在 workflow 中显式安装；仓库不再依赖不存在的 `requirements-dev.txt`。
- 静态交付门禁继续运行 `static_scan`；`doc_gate` 只检查当前 CI 工作流说明，历史 Markdown 归档不再阻断 PR。
- CI 额外检查 Codex 硬切后旧链关键字不会回到运行代码、测试、脚本或 prompts。
