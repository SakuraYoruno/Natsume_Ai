受众：MASTER / CODEX
标题：当前代码清理执行包（2026-09-09）

# 当前代码清理执行包

基线：`6dd9d55dcd0c27fad1407bff00817d50e8c8e74c`，分支 `codex/consolidate-shared-infrastructure`。
本包起初是分析、计划和只读校验工具；当前工作树已经按其中的 C01-C10、G02、P01、P02、R00、S00、F00 和 T00 前两批
进行了受限实施或取证。所有变更仍未提交或推送，完整应用验收尚未完成。

## 结论

当前主要债务是“迁移未收口 + 隐式导入 + 源码布局约束 + 能力接入不完整”，不是尚有几十万行已证明无用的代码。
原审计基线为 `ff812848`，其中许多第一轮候选已被删除，不能重复执行旧清单。用户的能力保留要求高于旧审计中的 DELETE 建议。

| 本轮复核项 | 结果 | 不能据此推导的结论 |
|---|---|---|
| Git 跟踪 UTF-8 文本物理行 | 1,414,844 | 不是 141 万行有效业务逻辑 |
| Python 文件/物理行 | 3,063 / 1,202,218，AST 解析错误 0 | 不是运行/类型检查通过 |
| Host / tests | 729,678 / 421,953 | 测试不可按“占比高”删除 |
| 单次调用转发候选 | 1,515 个，10,410 行 | 包含合理适配、构造和校验，不能批删 |
| 条件式提前退出后的实现 | 223 个方法，6,651 行，23 个文件 | 非 canonical 组合仍可执行，不能称绝对死代码 |
| 运行源码中函数开头无条件 return/raise 的尾部候选 | 0 | 不代表没有其他形式死代码 |
| Pentest 工具注册 | 89 个 ID；执行器源码均有对应字面量 | 不代表 89 个能力均已启动、获授权或完成执行 |
| 相对原审计基线净变更 | -84,829 行 | 不是全部删在生产代码，也不代表能力未退化 |
| 临时分支相对 dev | 31 个提交，净 -2,484 行 | 不是架构整合已经完成 |

这些计数不含未跟踪的本计划；包含注释、空行、配置和数据。58 个二进制文件不计文本行。
统计按静态规则定义，不是覆盖每条运行路径的穷尽正确性证明。

## 使用顺序

1. 读 [分析与决策](ANALYSIS.md)，尤其是不可删除边界。
2. 读 [小模型执行协议](RUNBOOK.md)。
3. 从 [机器任务清单](tasks.json) 选一个任务；`READY` 才允许按依赖顺序执行，`EVIDENCE_ONLY` 只能写证据，`HOLD` 不允许改生产行为。
4. 用 `py -3 -B docs/50_project/cleanup_20260909/check_plan.py --task G00` 取得该卡。
5. 每次只做一个任务或一个器官的至多 3 个已批准方法；未通过的任务不能计为清理完成。

执行时的仓库根目录是 `D:/nastume/Natsume_Ai`。本包内相对源码路径均相对此根目录。
没有固定该目录的执行环境应先通过 `git rev-parse --show-toplevel` 定位，不要复制绝对路径到另一台机器。
初始共有 48 张任务卡：12 张 `READY`（仍须满足前置证据）、9 张
`EVIDENCE_ONLY`、27 张 `HOLD`。任务 JSON 是原子执行白名单，不是实时完成
看板；当前实施事实以 receipts 和本 README 的“当前执行与测试口径”为准。
HOLD 中包含 23 个器官文件的条件迁移，不能交给小模型自由裁决；没有任何整文件删除被预先授权。

## 给小模型的直接启动提示词

```text
先完整阅读 docs/50_project/cleanup_20260909/RUNBOOK.md。
以 tasks.json 为唯一任务清单，本次只执行 G00 和 G01 的只读证据工作。
已有审计报告只作候选证据，禁止直接照旧报告删除代码。
不安装依赖、不启动 Host/Edge/真实靶场、不调用外部工具、不改运行配置、不提交推送。
发现依赖缺失时记录 BLOCKED_ENV，继续不依赖该环境的静态检查。
输出精确到文件和符号的证据、测试失败阶段、下一张可执行任务卡；不得宣称整个系统通过。
```

后续实施提示词：

```text
执行 tasks.json 中的 <任务ID>，先运行 check_plan.py --task <任务ID>。
读 RUNBOOK.md 和该卡全部 read 文件；核对前置任务证据与本次 Git 起点。
只修改 edit/create/delete 白名单。READY 不免除前置条件；HOLD 不得自行解锁。
逐项满足 new_tests/invariants；测试收集失败、仅 mock 成功或零用例都不是 PASS。
完成后运行该卡测试和 check_plan.py --task <任务ID> --check-diff --base <本次起点提交>。
记录改了什么、保留了什么、行为证据与剩余限制。不要顺手扩大范围，不提交推送，等待用户指令。
```

## 文件说明

- `ANALYSIS.md`：当前调用链、债务分级、覆盖范围及保留理由。
- `RUNBOOK.md`：执行/测试/回滚规则、机械修改方法、人工解锁要求。
- `tasks.json`：带依赖、精确路径白名单、步骤、测试和停止条件的任务卡。
- `baseline.json`：全仓普查输出，包含 223 个方法的条件、源码位置和后缀大小。
- `census.py`：仅解析 Git 跟踪文件，不导入应用的可重跑普查器。
- `check_plan.py`：检查任务路径/依赖图和改动范围；不执行清理，不代替测试或授权。
- `test_plan_tools.py`：执行包自身的离线校验测试，不导入应用或执行 Pentest。

## 当前测试口径

已完成的低风险清理只移除了 package-root convenience exports、一个私有单跳转发和一个
对生产行数的偶然断言；底层子模块、迁移、审计、Pentest/Lab、授权和执行器均被保留。
对应 C01-C10 的 import-boundary 测试、C04 artifact read-model 测试、C05/C06 定向测试及
G02 CI scope 测试已运行，其中该组最新结果为 14 passed。

G01 的七个包根均已有闭合证据；详见 `receipts/G01-import-surface.json`。G02 已将
`tests/security` 纳入稳定的 contract/integration/security CI job，未改变其排除标记或将
强门禁降级为 `continue-on-error`。

P01 新增了统一 Skill 到 Pentest lab adapter 的 RECORDING_ONLY 连通性测试：高风险
Skill 没有 `review://` 不得触发 recorder；批准后的同一幂等调用只记录一次，replay 不得
重复调用；target drift 必须拒绝。该测试在本机已收集后明确 skip，因为 Python 3.11.1
缺少项目 requirements 声明的 `cryptography`，且 canonical effect digest 依赖它。

P02 已发现并记录一个需要设计的效果真相分叉：统一 Skill 路径在 executor 异常后会将
效果投影为 false，而 SecureRun 有 NOT_EXECUTED/EXECUTED/UNCERTAIN 三态与持久化对账。
详见 `receipts/P02-effect-truth.md`。该卡是 HOLD；不得以删除 SecureRun、LabCapability 或
Pentest executor map 的方式“解决”。

S00 已区分未接入的 `CredentialVault` 类与实际由已授权执行器使用的运行期
`credential_vault` 映射；后者必须保留。收据还记录了 NTLM hash 进入 attack graph 返回值、
显式空 evidence policy 可关闭脱敏以及 resolver 不自行限制 target/protocol 的边界。详见
`receipts/S00-credential-flow.md`；其现有合成数据测试为 30 passed，未读取任何真实凭据。

R00 已重核 TaskStorePort 87 个方法、DurableTaskStore 38 个 mixin、83 个函数级/33 个聚合
静态接收者和 4 个具体构造点。`assert_node_verification_ready` 与
`get_acceptance_effect_intent` 均有额外的非类型化真实消费者，不能按 AST 未见调用删除。详见
`receipts/R00-port-usage.json`。相邻的非布局结构测试为 17 passed；更广的五文件选择中
有一项基线已存在的行数阈值失败（模块在基线和当前均为 1008 行，而断言为小于 1000），本轮未改写它。

F00 已证明 `include_legacy` 虽在服务端默认关闭，监控 UI 仍显式请求它；Pentest 元数据、审批和
UNKNOWN 状态也仍是当前操作面。SecureRun 默认 READ_ONLY，并通过统一 Skill 的已绑定 authority、
grant、review、target/action/args digest 和一次性 capability 保护真实 effect；旧环境开关只是继续收紧
和观测，不能替代授权。`HOST_SKILL_PENTEST_ENABLED` 是默认启用的统一 Skill provider，但其外部部署
配置仍为 UNKNOWN，不能删除。详见 `receipts/F00-operational-closure.json`；纯前端 read-model 为 7 passed，
兼容注册表/退役路径合同为 2 passed。SecureRun 两个测试模块在收集时因本机缺少声明依赖 `cryptography`
而 BLOCKED；完整 release-manifest 合同还存在基线已带的 migration head 断言滞后（期望 0153、实际 0154），
本轮未改写它。

T00 已完成全部条件尾部候选的十二批、共 223 条取证：第一批为 16 个 ANS、4 个 cerebellum 方法；第二批为
3 个 cerebellum、8 个 circadian、9 个 Cortex 方法；第三批为 7 个 Cortex、5 个 DefaultMode、8 个
Endocrine 方法；第四批为 2 个 Endocrine、7 个 Glymphatic、10 个 Hippocampus、1 个 InitiativeGovernor
方法；第五批为 6 个 InitiativeGovernor、5 个 Interoception、6 个 Language、3 个 Limbic 方法；第六批为 20 个
Limbic/BodySense/Silence 方法；第七批为 2 个 Limbic、8 个 MirrorNeuron、6 个 MotorCortex、4 个 PainReward
方法；第八批为 18 个 PainReward、2 个 Peripheral 方法；第九批为 7 个 Peripheral、5 个 Prefrontal、3 个
Prefrontal event handler、2 个 Proprioception 方法；第十批为 9 个 Proprioception、11 个 SocialBrain 方法；
第十一批为 9 个 SocialBrain、7 个 Somatic、4 个 Vestibular 方法；第十二批为 3 个 Vestibular 回调。
累计 134 条 `UNIQUE_NEEDS_MIGRATION`、42 条 `LIVE_NONCANONICAL`、47 条 `UNKNOWN`，没有任何
`EQUIVALENT_TESTED` 或删除结论。canonical Life 没有旧 EventBus 名称的一对一适配器；第三批确认 Cortex 的
TASK_SUCCESS 会影响 GraphRuntime 实际读取的 GovernorRulesCache，DefaultMode 的内在活动有统一 Skill、器官和
事件入口，Endocrine 的兼容缓冲有压力/关系/孤独信号的真实写入路径。第四批进一步确认恢复窗口具有生命周期、
昼夜、默认模式和内感受多条入口，三层记忆写入和投影仍有恢复、Prefrontal、Cortex context 消费者。第五批
确认 Initiative 仍在 Cortex 路由和 Motor 决策前置中参与执行，Interoception 有生命周期调度入口，Limbic 的
时间线、持久情绪和静默状态仍有兼容消费者或未迁移语义。第六批进一步确认统一 BodySense/Silence Skill 与
BODY_SENSE_UPDATED、BOND、TASK、HUMOR、LONELINESS、IMMUNE 等事件链仍写入 Limbic 兼容状态。第七批确认
MirrorNeuron 的 Edge/对话共鸣与状态投影、MotorCortex 的 Edge 回执/Cerebellum 重试、PainReward 的持久 hedonic
写入和表层读模型均仍有可达行为；无已跟踪生产调用的旧模型提示/计划 API 与无已跟踪发布者事件保持 `UNKNOWN`。
第八批进一步确认 PainReward 的 hedonic 写入、衰减、JSON 持久化、Cortex/Endocrine/Limbic 读投影和 Peripheral
状态/订阅桥都仍有上下游；Language 的 `EMOTION_STATE_CHANGED` 与
`BOND_STAGE_CHANGED` 没有已跟踪 Python 发布者，但 Edge/外部协议仍未知，因此保持 `UNKNOWN`，不能按静态
零匹配删除。第九批确认 Gateway 生命周期会发布平台连接/断开并驱动 Peripheral 快照及受限器官反馈；外设快照、门控、
静音只有合同/测试或动态命令入口，仍保持 `UNKNOWN`。Prefrontal 的交互、Bond 代理、孤独与免疫成长链仍有真实路径，
而自传记忆公开入口和无已跟踪发布者的 `BOND_STAGE_CHANGED` 回调不形成删除结论；Proprioception 状态转移仍写 registry、
发布 `PROPRIOCEPTION_UPDATED` 并被 Cortex、Initiative、Interoception 消费。第十批确认 Proprioception 的导出与状态转移
仍有真实上下游，而会话/通道/语音/同步任务入口缺少可跟踪根调用时保持 `UNKNOWN`；SocialBrain 的 scheduler summary、
Bond vector 和关系事件链仍有读写、事件或受限器官反馈，公开 projection 与无发布者的阶段事件不形成删除结论。第十一批确认 SocialBrain 的修复投影、幽默关系反馈和 Cortex 导出仍有生产依赖；rupture/repair 主题无已跟踪发布者、公开叙事/里程碑入口则保持 `UNKNOWN`。Somatic 仍有 BodySense/孤独/Edge 回传与 Cortex 投影；Vestibular 仍有模式、平台、主动性输入以及稳定性事件和受限器官反馈。第十二批确认模式切换、Edge 平台连断和主动性决策均会按事件主题到达 Vestibular 回调；三者均触发带状态、事件和受限器官反馈的稳定性评估。至此 T00 的 223 条均完成取证，零项达到 `EQUIVALENT_TESTED`，不解锁生产删除。核心纯单元测试依次为 27 passed、16 passed、15 passed、11 passed、55 passed；依赖正常 fixture 的
补充模块为 13 passed、52 passed。第四批首次核心选择引用了不存在的 `test_hippocampus_agent.py`，pytest 未运行
用例，随后已用实际存在文件复跑通过；该选择错误不是产品失败。`bond_dynamics` 给 `emit_bond_progress` 传入
不存在的 `bond_stage` 参数且吞掉异常，仍需单独设计修复。详见 `receipts/T00-method-equivalence.json`；T00 已覆盖
全部 223 条，T01/T02 仍为 HOLD，本轮未解锁任何生产修改。第六批的 Limbic/BodySense/Silence 离线复核为
46 passed；第七批的 MirrorNeuron 与 MotorCortex/PainReward 复核分别为 34 passed、75 passed；第八批的
PainReward/Peripheral 复核为 39 passed；第九批 Peripheral/Prefrontal/Proprioception 纯单元合同复核为
59 passed；第十批 Proprioception/SocialBrain 的纯单元与隔离状态合同复核为 50 passed；第十一批 SocialBrain/Somatic/Vestibular 与模拟 Host-Edge 回环合同复核为 66 passed；第十二批 Vestibular 回调复核为 5 passed。T00 的全量矩阵已封存，不提前形成删除结论。

因此当前不宣称完整应用、安全测试、审批弹窗或自主 Agent 闭环已经通过。不要给清理预设
删除行数 KPI；每批同时报告退役实现数量、调用层数、能力可达性、协议/迁移完整性和测试
结果。

执行包结构校验仍通过：48 张任务卡路径/依赖图通过，10 项工具离线自测通过，223 个条件
候选均有对应 HOLD 卡。当前工作树存在未提交的实施变更；上述校验不代替完整业务验收。
