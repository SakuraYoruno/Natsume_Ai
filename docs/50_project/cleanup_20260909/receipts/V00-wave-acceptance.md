# V00 波次验收与第二轮孤儿复核

- task_id: `V00`
- start_head / end_head: `6dd9d55dcd0c27fad1407bff00817d50e8c8e74c` / `6dd9d55dcd0c27fad1407bff00817d50e8c8e74c`
- state: `BLOCKED_ENV`
- executed_at: 2026-09-11
- task scope: evidence only; no production, test, configuration, migration, Pentest, Lab, Autonomous, authorization, or approval behavior was changed by V00.

## 起点与已有工作树

V00 开始时工作树已包含 15 个已跟踪修改和 11 个未跟踪测试文件，以及整个未跟踪的清理执行包；它们早于本任务，未被重置、覆盖或改写。V00 仅新增：

- `docs/50_project/cleanup_20260909/after-wave.json`
- `docs/50_project/cleanup_20260909/receipts/V00-wave-acceptance.md`

已跟踪的本波实现/测试/CI diff 为 15 文件、26 行新增、446 行删除。主要组成是七个包根 convenience export 的清除、ArtifactStore 私有 `_sealed_digest_for_ref` 单跳回跳的移除、两项测试偶然耦合的收紧，以及将 `tests/security` 加入默认强制 contract/integration job。该 diff 的实际所有权需由提交审阅时结合前序任务确认；V00 不把已有修改冒充为自己的实现。

`check_plan.py --task V00 --check-diff --base 6dd9d55dcd0c27fad1407bff00817d50e8c8e74c` 返回 exit 2：列出上述 15 个 tracked 文件及 10 个未跟踪测试为 `OUT_OF_SCOPE`、`TOO_MANY_FILES 25 > 3` 和 `TASK_HAS_NO_CODE_MUTATION_AUTHORITY`。这是证据任务从脏工作树起步的预期范围信号；没有执行 reset、checkout、删除或覆盖来伪造范围通过。

## 普查与二阶闭包

`py -3 -B docs/50_project/cleanup_20260909/census.py --output docs/50_project/cleanup_20260909/after-wave.json` 成功，且没有覆盖 `baseline.json`。

| 项目 | baseline | after-wave | 结论 |
|---|---:|---:|---|
| Python 物理行 | 1,202,218 | 1,201,798 | 净 -420；Host -418、tests -2 |
| 单调用候选 | 1,515 / 10,410 行 | 1,514 / 10,408 行 | 唯一已收口的单跳候选与 ArtifactStore 私有回跳一致 |
| 条件尾部候选 | 223 | 223 | 没有按候选自动删代码 |
| 无条件尾部候选 | 0 | 0 | 无新增 |
| 退休门 | 21 | 21 | 结构逐项相同 |
| Pentest 注册 ID | 89 | 89 | 静态能力表逐字相同 |
| 缺失 executor 字面量 | 0 | 0 | 未发现能力连续性退化 |

T00 已封存全部 223 条逐方法矩阵：134 项 `UNIQUE_NEEDS_MIGRATION`、42 项 `LIVE_NONCANONICAL`、47 项 `UNKNOWN`、0 项 `EQUIVALENT_TESTED`。因此本波不产生任何新的生产删除授权。

七个包根当前都仅保留文档字符串和空 `__all__`：`tools`、`audit`、`release`、`persona`、`runtime`、`integrations`、`tribe`。重查没有发现 tools/audit/release 的直接或动态包根消费者；persona/runtime/integrations/tribe 命中的语句均导入其仍存在的物理 child module，而非已删除的类/函数便利导出。对应 import-boundary 测试在本波本地通过。未发现可在 V00 自动删除的二阶孤儿；剩余 1,514 个单调用候选和所有 HOLD 迁移项只可作为下一轮候选。

## 能力与授权不变量

- Pentest 算法/契约门禁：`38 passed in 4.40s`，使用显式后台自动任务关闭变量，未连接真实目标。
- `after-wave.json` 的 89 个 Pentest 注册工具和 executor 字面量闭包与基线完全一致。
- P01 的统一 Skill 到 Pentest Lab 连通性用例在缺少 `cryptography` 时明确 skip；没有伪造 recorder/真实效果通过。
- 任何危险执行、Pentest、Lab、Autonomous 和 Agent 能力均未删除、降级、改风险级别或绕过现有统一 Skill、authority、grant、review、target/action/args digest、一次性 capability 与 LabCapability 边界。
- CI diff 将 `tests/security` 加入 PR/main 的强制 contract/integration job；未添加 `continue-on-error`，也未放宽外部 marker 排除。未取得远端 CI 运行证据，故状态为 `NOT_RUN_REMOTE_CI`。

## 本地门禁记录

| 门禁 | 结果 | 说明 |
|---|---|---|
| 最小环境/三个安全模块导入 | `LOCAL_PASS` | Python 3.11.1；pytest/fastapi/pydantic 可用；`cryptography` 不可用，三个指定导入仍成功 |
| Pentest 本地算法与契约 | `38 passed` | 官方 V00 命令，纯受控 fixture |
| Pentest/Lab 安全边界 | `BLOCKED_ENV` | `tests/security/test_pentest_secure_execution.py`、`test_pentest_secure_authority.py`、`test_task0491_lab_isolation.py` 在收集时缺少 `cryptography` |
| ArtifactStore 结构 | `2 passed` | 官方门禁中的 unit 文件单独确认 |
| Durable node artifacts | `REVALIDATED_BY_SPLIT_RUNS` | 19 个用例：初始 verbose 运行逐项显示前 15 个通过；第 16 个单独 `1 passed`，其余 3 个 `3 passed`。组合命令的终态输出未被执行通道保留，故不标整条官方组合命令为直接 `LOCAL_PASS` |
| canonical Life/relationship | `25 passed in 19.68s` | 官方 V00 命令 |
| TaskStore contract | `BLOCKED_ENV` | `tests/contract/test_nle_task_store_contract.py` 收集时缺少 `cryptography` |
| governed durable DAG | `NOT_RUN_COMPLETE` | 56 个已收集；组合运行只获得 8 个进度点、无终态；后续第 9 个用例单独 `1 passed`。不得外推其余用例通过 |
| 清理边界/Artifact/CI/Pentest 连通性 | `27 passed, 1 skipped, 3 failed` | 1 个 P01 recording 用例因缺少 `cryptography` skip；3 个失败都在既有 `test_dag_scheduling_runtime_structure.py` 的 1,506 行阈值与两个冻结 AST hash，不由 V00 修改 |
| 其余 READY 卡目标测试 | `46 passed in 2.41s` | coordinator、persona、runtime、integrations 与 Edge Tribe composition 的补充离线测试均通过 |
| C10 FastAPI 入口目标测试 | `BLOCKED_ENV` | `test_fastapi_app_bond_provider_social_brain.py` 导入链在 `host_brain.memory.canon_apply_service` 处因缺少 `cryptography` 收集失败 |
| 计划工具 | `10 passed` | `check_plan.py` 也报告 48 张任务卡有效 |
| Delivery Gate | `NOT_RUN_COMPLETE` | 本地门禁运行未生成临时报告且未返回终态；不记为通过 |
| 前端 read-model/typecheck/build | `NOT_APPLICABLE` | 本波没有 `host_brain/app/monitor_ui` 文件改动；不启动前端或真实服务 |

## 失败归因与后续限制

`cryptography` 是项目声明依赖但本机未安装；没有执行 `pip install`。它阻塞 SecureRun authority、Lab isolation 与 TaskStore contract 的测试收集，不能被解释为功能删除安全，也不能被解释为安全门禁通过。

复核后确认没有可直接采用的合规替代环境：仓库中不存在 `.venv`、`venv`、`env`、`.python` 或 `tools/venv` 解释器；Windows 已注册的唯一解释器是 Python 3.11.1，且缺少该包。Codex 捆绑 Python 是 3.12.14 且含 `cryptography 50.0.1`，超出 `requirements.txt` 的 `cryptography>=46.0.0,<47` 约束，故没有用它运行项目门禁。

`test_dag_scheduling_runtime_structure.py` 的三个失败是已有结构基线差异：当前 `dag_scheduling_runtime` 为 1,506 行，低于 1,500 行的断言不成立；`execute_stream` 和 `_validated_node_grant_refs` 的冻结 AST SHA-256 也已漂移。当前 diff 只移除了 coordinator 的 275 行偶然布局断言，未修复或掩盖上述三项失败。该问题需要单独的、经审阅的测试/结构任务，不能在 V00 中顺手修改。

下一步需要用户或环境所有者提供已核实的 `cryptography` 依赖后重跑安全与 TaskStore 门禁；并在可稳定观察的执行环境中完成 56 项 governed-DAG 门禁与 Delivery Gate。所有 `HOLD` 任务（P03/P04、R01、T01-T23、T90）仍需新的审阅批准子卡；V00 不会自动升级它们。
