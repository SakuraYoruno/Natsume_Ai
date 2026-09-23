受众：CODEX
标题：小模型清理执行协议

# 小模型清理执行协议

这不是“看着不合理就删”的授权。你执行的是一张明确任务卡，不负责重新设计整个系统。
分析阶段只能生成本执行包；用户另行要求实施后，才按下列规则改业务代码。

## 1. 状态与顺序

| 状态 | 允许动作 | 成功定义 |
|---|---|---|
| EVIDENCE_ONLY | 读代码/历史/测试；写 receipts 内证据 | 指定矩阵/失败原因完整，不是业务已修复 |
| READY | 前置证据成立后，仅改本卡白名单 | 不变量 + 目标测试 + 本次diff范围全部通过 |
| HOLD | 阅读和解释阻塞；不改生产代码 | 不能由执行模型自改 READY；先有审阅批准的新子卡 |

顺序：

1. G00 固定起点与环境；G01 做包根导出闭包。
2. C01/C02/C03，之后 C07—C10：逐包消除隐式导入，每包独立验证。
3. C04/C05/C06：一个私有回跳、一个偶然行数断言、两个测试耦合点。
4. G02：把稳定安全测试纳入强制 PR 门禁。
5. P00/P01/P02：能力表、真实统一授权链的 recording 正向验证、执行后不确定性。
6. T00 和 R00/F00/S00 可分开收集证据；T01—T23、T90、P03/P04、R01 全部需要审阅解锁。
7. 每一波 V00 验收，形成下一轮候选。没有可执行卡就停，不循环输出“继续优化”。

不要把 T 系列 HOLD 当作已批准删掉 6,651 行的清单。清理量可能小于候选量，功能迁移甚至可能先增加代码。

## 2. 每张卡的机械执行步骤

### PRECHECK

在仓库根执行：

```powershell
git status --short --branch
git rev-parse HEAD
py -3 -B docs/50_project/cleanup_20260909/check_plan.py --task C04
```

保存起点提交与已有改动的精确文件清单。若目标文件已有用户修改，读取完整 diff；不能重置/覆盖，不清理不相关修改。
HEAD 不同于本计划基线不等于失败，但必须重新核对本卡所有 read 文件和调用证据；不能只按旧行号下刀。
逐个阅读 read 文件，按符号定位；行号只是基线提示。

### EVIDENCE

优先 `rg` / `rg --files`；本机不可用时用 `git grep`、`git ls-files` 和 AST。

```powershell
git grep -n -F '_sealed_digest_for_ref' -- host_brain tests scripts tools configs docs
git log -S '_sealed_digest_for_ref' -- host_brain/agent_runtime
```

搜索需覆盖生产、测试、脚本、配置、装饰器、事件注册、provider 字符串、CLI/API 入口和外部兼容约定。
方法同名不证明接收者相同；函数 AST 同形不证明默认值、闭包、全局常量、装饰器、锁和 authority 相同。
`__init__` 导出删除需完成 G01；直接 import 子模块不跳过父包初始化。

### EDIT

- 只使用补丁编辑，仅限 edit/create/delete 白名单；不得通过格式化扩大到整个仓库。
- 一卡一切片；器官迁移一次最多 3 个已批准方法；不把几十个方法整体交给批量替换脚本。
- 不增加万能 Context、转发代理、自动 __getattr__、第二套 Skill/风险/审批模型。
- 外部拒绝边界、身份/授权/幂等/防重放、数据库迁移和 canonical Life 不变量优先。
- 新发现的孤儿只能记下一张候选，不能顺手删。
- 当前清单 delete 全为空：没有任何整文件删除被预先授权。方法级删除只按卡中精确步骤。

### VERIFY

先执行目标测试，再按 GATE_COMMANDS 执行该波相关门禁。
无变化的基线失败不能自动变成通过；如属于环境缺失，说明与当前变更的关系并阻止发布结论。
只有 mock/录制底层效果可以替代危险 IO；不能 mock 掉正在验证的授权器、准入器或持久化契约。
recording 测试成功应写 RECORDING_PASS，不能写真实靶场 PASS。

```powershell
git diff --check
py -3 -B docs/50_project/cleanup_20260909/check_plan.py --task C04 --check-diff --base <本次起点提交>
```

范围检查只验证 diff 是否越界，不证明语义、安全或测试通过；空 diff 也不能算任务完成。
检查器比较指定提交之后的全部工作区差异。已有用户改动会被一并报告；不要为让检查器绿色而撤销它们，应保存起点证据并请审阅者区分来源。
校验器忽略执行包自身生成证据，只为避免计划文件被误计入业务改动；这不授权修改任务状态来绕过门禁。

### RECEIPT

仅把必要证据写入 `docs/50_project/cleanup_20260909/receipts/<任务ID>.md` 或卡指定 JSON；不要保存密钥、模型原文、真实凭据或大段运行日志。

```text
task_id:
start_head / end_head:
state: EVIDENCE_COMPLETE | LOCAL_PASS | RECORDING_PASS | BLOCKED_ENV | BLOCKED_DECISION | FAILED
exact_changed_files:
old_behavior / replacement_behavior:
preserved_invariants:
reference_closure: ordinary / dynamic / external / test
tests: command, Python/environment, collected, passed, failed, skipped, exit_code
failure_stage: import | collect | setup | assertion | external_dependency
remaining_gap:
review_needed:
```

未收集任何测试、用例全部 skip、只做 AST/compile、仅接口返回 REJECTED，都不是能力验收通过。
只有用户明确请求时才提交/推送；提交中文，一卡一提交。当前请求只是制定方案，不授权实施或发布。

## 3. GATE_COMMANDS

以下命令在仓库根运行，解释器以 G00 核实的项目环境为准；示例使用本机 `py -3`。
缺少 cryptography 等依赖时不能自动 pip install 到用户全局环境；报告所需依赖并等待环境授权，或使用已经存在且核实完毕的项目环境。
不要把本机错误地缺少声明依赖当作删除安全代码的依据。

运行前阅读所选测试 fixture；仅允许纯单元、临时目录和 localhost 测试装置，不启动 Host 主服务或另一台 Edge。
环境变量只在本次测试进程内设置：`HOST_BACKGROUND_TASKS_ENABLED=false`，以及 CI 中已有的三项 `HOST_CODEX_IMMUNE_*` 自动运行关闭项。
如果测试需要真实模型、外部服务或硬件，记录 NOT_RUN_EXTERNAL；没有用户授权不启动。

### 最小环境检查

```powershell
py -3 -B -c "import sys, importlib.util; print(sys.executable); print(sys.version); print({k: importlib.util.find_spec(k) is not None for k in ('pytest','cryptography','fastapi','pydantic')})"
py -3 -B -c "import host_brain.tools.contracts"
py -3 -B -c "import host_brain.pentest.tools.tool_specs"
py -3 -B -c "import host_brain.release.compatibility_registry"
```

这三个 import 各自单独进程，避免之前导入过的模块/全局替身掩盖耦合。只导入，不构造应用或调用 executor。

### Pentest 本地算法/契约检查

```powershell
py -3 -B -m pytest -p no:cacheprovider -q tests/unit/test_pentest_agent_loop_contracts.py tests/unit/test_pentest_strategic_planning.py tests/unit/test_aw05_pentest_model_authority_cutover.py tests/unit/test_pentest_cognitive_kernel.py tests/unit/test_pentest_autonomous_evidence.py
```

前一轮 38 passed 使用了 `--noconftest`，不是本条标准命令的通过证据。认知测试会构建 localhost HTTP fixture，不能说完全无网络 IO。

### 现有安全边界

```powershell
py -3 -B -m pytest -p no:cacheprovider -q tests/security/test_pentest_application_boundary.py tests/security/test_pentest_secure_execution.py tests/security/test_pentest_secure_authority.py tests/security/test_pentest_secure_ingress_authority.py tests/security/test_task0491_lab_isolation.py
```

这些是必要的拒绝与 secure-run 验证，不替代 P01 对统一 Skill/Broker 正向链路的验证。

### 产物读侧、Tribe/Life、持久化

```powershell
py -3 -B -m pytest -p no:cacheprovider -q tests/unit/test_ne01_artifact_store_structure.py tests/integration/test_ne01_durable_node_artifacts.py
py -3 -B -m pytest -p no:cacheprovider -q tests/unit/test_dl01_affective_life_kernel.py tests/unit/test_dl03_relationship_lifecycle.py tests/unit/test_aw04_life_domain_reducer.py tests/unit/test_dl06_canonical_relationship_prompt_projection.py
py -3 -B -m pytest -p no:cacheprovider -q tests/contract/test_nle_task_store_contract.py tests/integration/test_gr03a_governed_durable_dag.py
```

Tribe 具体子卡另须包含自身 retirement/positive tests。混合测试不能整文件移除。
持久化结构重构还必须验证 CAS 冲突、租约失效、取消与写入竞争、事务中途故障回滚、重启重放、owner/tenant 隔离；现有测试不足时先补，再改结构。

### 前端（仅相关波次）

在 `host_brain/app/monitor_ui` 中使用已存在的依赖运行：

```powershell
npm run test:read-model
npm run test:operator-client
npm run check:operator-client
npm run typecheck
npm run build
```

不自动 npm install，不运行 `test:e2e:live-external-status` 或其他真实服务验收。前端静态构建通过不等于审批弹窗/恢复体验验收通过。

## 4. T00 的方法级迁移矩阵

每条至少包含：

```text
path, symbol, baseline_line, guard_condition
old_inputs, old_outputs, exceptions, mutable_state, side_effects
production_callers, offline_callers, event_or_dynamic_callers, external_contract
canonical_owner_path, canonical_owner_symbol
equivalence_test_path, test_case, exact_result
unique_behavior_to_migrate
noncanonical_support_decision
disposition: EQUIVALENT_TESTED | UNIQUE_NEEDS_MIGRATION | LIVE_NONCANONICAL | UNKNOWN
reviewed_subtask_edit_files, reviewed_subtask_test_files
```

若 canonical 只返回退休回执而旧实现提供真实分析，不得判 EQUIVALENT_TESTED。
用户要求保留的 Pentest/实验室、器官分析、知识经验或外部操作能力，优先迁移，不以“无调用”作为处置结论。
保护 ANS 非 affect 事件、Tribe 应用实例隔离、用户与self状态分离、canonical 唯一写者和关系scope。

## 5. PENTEST_CONTINUATION_SPEC（P03 解锁前必须完整）

每项都要写现有模块/方法/契约、测试和恢复规则；只写箭头图不合格。

1. **规划输出**：下一步 tool_id/args/target/evidence/stop_reason；明确只是候选，不附带 authority/token。
2. **发现完整性**：统一 SkillRegistry 中的能力描述与 risk 是来源；新增能力注册仍只有一套。
3. **准入与图演进**：哪个现有入口把候选冻结成节点/新 revision；不能改已冻结参数，不能局部越过正式 admission。
4. **审批**：用哪个现有 durable review 服务暂停、批准、拒绝、过期、撤销；授权绑定具体身份、节点和参数。
5. **执行**：现有 Broker 绑定服务器 executor；模式/target/单次 capability 检查保留。
6. **结果真实性**：未进入executor才可确认为未执行；进入后异常/保存失败按既有 UNCERTAIN/reconciliation 合同处理，不盲重试。
7. **反馈与恢复**：只把持久化、确认的结果交回 planner；进程重启后以持久日志恢复，不能复制本地循环计数当真相。
8. **能力迁移**：旧循环的预算、熔断、重试、知识/evolution、adversarial、Swarm、收尾逐项有去向。
9. **验收**：宽目标进入规划、至少两步 recorder 执行、高风险中断与恢复、拒绝/取消、重启、重放、参数漂移、未知效果停止；全部使用受控 recording fixture。

若必须修改持久协议或签名字段，先提交设计决策；不把这种更改藏进清理 PR。不能给小模型一个“把 Agent 接起来”的无边界任务。

## 6. 失败与回滚

- 失败后先解释：环境、测试基线、行为回归、未完成替代还是范围冲突。
- 不删除失败测试、不降低风险级别、不硬编码 approve、不添加 fake-success。
- 不用 reset --hard / checkout -- / 清空目录恢复现场。
- 尚未提交时，按本次补丁逐项撤销自己的变更，先核对没有用户同期改动；存在重叠就请求方向。
- 已提交的独立任务优先在用户授权下 revert 该提交；不得回退数据库、重写 migration 或强推分支。
- 连续遇到相同环境阻塞不反复跑相同失败命令；保持证据任务可继续，改代码任务暂停。

## 7. 工具验收

```powershell
py -3 -B docs/50_project/cleanup_20260909/check_plan.py
py -3 -B docs/50_project/cleanup_20260909/test_plan_tools.py
py -3 -B docs/50_project/cleanup_20260909/census.py --output docs/50_project/cleanup_20260909/after-wave.json
```

`check_plan.py` 检查所有路径、任务依赖无环及白名单；只输出 PLAN_VALID/SCOPE_VALID，不输出业务 PASS。
保持 baseline.json 不覆盖。普查器输出是候选证据，不会删任何源码。
