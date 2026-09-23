受众：MASTER / CODEX
标题：当前架构分析与清理决策

# 当前架构分析与清理决策

## 1. 范围和置信度

全仓进行 Git 文本普查和 Python AST 扫描；人工追踪应用装配、Pentest 双入口、Tribe canonical 切换、包初始化、持久化聚合和结构测试。
不是逐行人工审核 140 万行，也没有运行外部服务栈、前端浏览器验收、Edge 或真实 Pentest。
旧审计来自 `C:/Users/v_wenloliu/Downloads/natsume_audit`，基线不同；下文只使用重新核对的当前事实。

分类含义：

- `PROVEN_LOCAL`：当前源码/静态结构可重复验证。
- `LOCAL_TEST`：指定环境和测试用例通过，不外推到整条生产链。
- `GAP`：检查的生产链中没有找到必要连接，必须正向验证，不能用拒绝测试替代。
- `CONDITIONAL`：仅特定组合下不执行；其他组合行为需要产品/迁移决策。
- `KEEP`：现有能力、安全、持久化或外部协议边界，不因行数/入度删除。

## 2. 规模与热点

| Host 区域 | 物理行 | 主要判断 |
|---|---:|---|
| app | 170,037 | HTTP、装配、Surface、监控 UI 混在同一大区域；不是全为转发代码 |
| persistence | 105,377 | 交易、租约、恢复和读模型；应缩短接口，不删一致性保证 |
| pentest | 65,658 | 工具、知识、自主规划、实验室执行；能力保留并核实接线 |
| persona | 52,553 | 陪伴表达、记忆、内在活动与兼容状态；先以生产主链归属划界 |
| task_runtime | 32,693 | 受治理持久任务，不用于强行包装每轮普通聊天 |
| tribe | 25,458 | 器官式分析 + 新旧切换分支；按方法迁移，不拆掉 Agent 体系 |
| agent_runtime | 22,837 | 模型尝试、产物、执行/验收；注意文件切分后的相互回调 |
| audit | 22,551 | 包含冻结 SQL 历史；严禁把全部视作运行冗余 |
| contracts | 22,483 | 跨层与持久协议；版本名不是删除证据 |
| skills | 20,899 | 统一发现/风险/执行绑定；Pentest 复用这里 |
| memory / digital_life | 17,215 / 16,271 | 当前记忆与唯一 Life 状态主链，保留 |

其余区域的精确统计见 baseline.json.host_packages。脚本/工具约 12.3 万行和文档约 6.2 万行单独计数；移动命名空间不计为删除功能。

## 3. 当前主链和应有边界

### 3.1 普通聊天与受治理工作

生产组合根是 `host_brain/app/asgi.py:create_app`。Host 在生命周期内构造应用局部安全、命令、Life、任务与 Tribe 服务。
`AgentRuntimeApplicationPorts.stream_realtime_chat` 明确允许普通陪伴进入 Surface 实时路径，不承担完整 TaskGraph/Attempt/Lease/Budget 成本。
外部写入、工具和高风险动作使用受治理任务链。清理不能把两者强行变成一条昂贵路径。

需要保持：应用实例隔离、生命周期启动成功后发布权威对象、关闭时撤回、聊天低延迟、用户 affect 与 self affect 隔离。
`ServiceProvider = Callable[[], Any]` 和 `Callable[..., Any]` 虽便于装配，却抹掉请求/返回契约。
具体例子：`contracts/host_lifecycle.py:HostLifecycleApplicationPorts` 36 个字段；`agent_runtime/application_ports.py` 20 个字段。
替换原则：只给当前消费者补窄类型，不新增万能 Context、ServiceLocator 或自动 getattr 注册层。

### 3.2 Pentest：单工具已接，宽目标自主闭环仍缺正向证明

核对路径：

```text
Thalamus 明确工具请求
  -> route=skill / pentest_*
  -> SkillRegistry / frozen TaskGraph / CapabilityBroker / durable review
  -> PentestSkillExecutionPort
  -> 一次性 LabCapability + server-owned executor

Thalamus 宽目标 / 通用委派
  -> pentest_agent_handler（只读）
  -> readonly_pentest_domain_handoff.v1
  -> 文本 + 审计结果；未发现由该结果继续驱动 AgentLoop 的生产桥

Domain Pack pentest.attack_planner
  -> production_composition._pentest_plan
  -> AttackPlanner._fallback_plan（确定性规划，不执行工具）
```

精确证据：

- `tribe/thalamus_agent.py:_build_pentest_skill_route_seed` 与 `_build_pentest_route_seed` 区分具体工具和宽目标。
- `skills/pentest_registry.py:PentestSkillProvider.get_skill_specs` 遍历同一 89 工具表，映射 canonical risk，risk>=4 要确认。
- `skills/provider_catalog.py` 默认启用内建 provider；注册失败会 warning 后跳过。因此源码有 ID 仍不能证明启动后已注册。
- `app/governed_delegate_dispatch.py:_project_readonly_outcome` 不把原始交接结构变成可执行图；其注释明确删去可执行结构。
- `domain_packs/production_composition.py:_pentest_plan` 调用 `_fallback_plan`。
- `pentest/autonomous_kernel.py:run` 与 `pentest/agent/kernel.py:run` 都委托 `PentestAgentLoop`；旧 `_run_legacy` / `_run_cognitive` 仍留存。
- `pentest/production_runtime.py` 默认 DISABLED，真实执行取决于服务端模式/配置和授权；不能通过更改默认值来“证明接通”。

决策：保留所有能力和安全验证。单工具的正向 recording 验证可以直接补；自主闭环的集成不是普通去重任务，必须先固定增量任务/审批恢复协议。
不能在 handler 里直接调用 kernel.run，不能新建第二套 SkillRegistry，不能把自然语言“已授权”变成执行许可。
LabCapability 是单次效果的 target/tool/args/replay 约束，当前不等同第二套风险策略；不能因名字多就删除。

自主闭环目标（需设计审阅后才实施）：规划器只产生下一步候选；现有任务准入冻结具体 Skill 节点；高风险节点等待现有 durable review；批准后执行；确认的结果再送回规划器。
必须复用现有 continuation/graph revision 机制，不能修改已冻结节点参数，也不能凭本地循环状态在重启后重复执行。若现有协议表达不了，先报告协议缺口，禁止小模型自造数据库/签名/令牌。

### 3.3 Tribe：条件性旧尾部，不是全局死代码

`asgi.create_app` 向 `build_tribe_application_ports` 注入延迟 Life command provider；后者构造 AffectFusionService 并选择 canonical authority。
`TribeApplicationPorts.affective_life_authority` 默认仍是 LEGACY_AFFECT_COMPATIBILITY；缺省离线构造可走另一分支。
21 个 gate 定义与 23 个条件尾部所在文件不是同一指标；Mixin/直接判断可不定义独立 gate。
223 个方法清单在 baseline.json.conditional_tail_candidates，约 6,651 后缀行，不包括所有复杂条件。

尤其要保留 `ANSAgent.emit_from_thread` / `_async_emit` 的非 affect 事件；它们的判断还包含事件类型，不能把整个函数归为退休路径。
同理，Cortex 的意图路由、Tribe 的注册/隔离、SocialBrain 的 scoped canonical 关系读投影、Prefrontal 的人格边界不能一起删。

每个旧方法必须归入以下一格：

1. 纯旧状态投影，canonical 有等价结果：迁移消费者和测试，然后去旧实现。
2. 旧写入已被禁止，但有独有分析/叙事功能：先迁移为纯分析输出，不能删除业务作用。
3. 只剩拒绝壳：确认内部调用已移走；外部接口的明确拒绝/410 行为仍按兼容边界保留。
4. 仍有受支持的非canonical消费者或替代不明确：HOLD。

旧审计“先删 canonical return 后的实现”跳过了第 2/4 类，因此本包不授权批量执行该步骤。

### 3.4 Task / Persistence：活平台过度展开

`persistence/task_repository.py:DurableTaskStore` 当前有 38 个基类，包含 Outbox、取消、审批、重试、预算、产物和多类 reconciliation；旧审计的 33 个不是当前数字。
`task_runtime/ports.py:TaskStorePort` 当前有 87 个方法，向许多局部消费者暴露大接口。具体消费者使用多少需按接收者对象分析；仅检索同名方法会误把其他类的调用算进去。
不可把没有 task_runtime 消费者的签名直接当成没有 app/composition/operator 消费者的存储方法。

保留：事务原子性、CAS、owner/tenant/workspace、fencing、幂等、撤销、重启恢复、审计/outbox 同事务、冻结 schema。
先整合纯输入校验/记录转换和不必要转发，后考虑接口分离；不扩大 Protocol 家族到每个方法一个接口，也不为了减少行数删除恢复能力。
`task_effect_reconciliation_mutation_store.py:record_graph_cancellation_effect_reconciliation_proof` 虽然长参数转发，但还做异常翻译，不能按纯转发删。

### 3.5 源码结构测试让“拆文件”代替“减复杂度”

具体证据：

- `tests/unit/test_dag_scheduling_runtime_structure.py` 要求 coordinator 恰好 275 行，并固定部分 AST。
- `test_task_dag_state_ready_claim_store_structure.py` 检查 Mixin 的 `__bases__` 顺序。
- `test_ne01_artifact_store_structure.py` 要求一批方法只有一个 return delegate。

这些测试与合法的无 mutation、禁止越层导入、guard-before-effect、事务要求混在一起。
清理只替换偶然布局断言；禁止整个文件删除、删除所有 AST 测试或重录哈希让失败消失。
必须先能指出对应行为测试：异常传播、事务回滚、重复/并发/取消、安全越权和结果一致性。

### 3.6 包初始化的隐式依赖

直接导入子模块仍会先执行父包 `__init__`，所以“把消费者改成直接 import”本身并不能解决宽初始化。
`tools/__init__`、`audit/__init__`、`release/__init__` 当前主要是便利导出；内部生产代码已多使用直接所有者路径。
先枚举普通 import、相对 import、模块属性访问、importlib 字符串、配置/脚本/文档用例和 pickle 等持久类型路径；闭包通过才去掉不用的包根出口。
不能用 `__getattr__` 懒加载把所有历史导出永久留下，也不能捕获 ImportError 后伪造类/空对象。
`model_runtime/__init__` 仍有多个测试/工具消费者，不能和零消费者包同批清空。

### 3.7 可确定的转发小切片

`AgentArtifactStore._sealed_digest_for_ref` 只转给 `artifact_store_read_model.sealed_digest_for_ref`；read model 内两个调用又经 store 绕回来。
可把两个内部调用改为本模块直接函数调用，去掉私有壳及结构测试里该项；保持 verified reopen、路径、seal digest、异常和只读边界。
这是可审查的局部缩短调用链，不意味着应合并整个产物读写模块。

`retrieval/recall_fusion.py` 为兼容 re-export 多个私有工具函数。
当前 `_map_route_exception` 的消费者包含两个测试，可按明确来源改到 `recall_fusion_utils`；但主类本身也可能使用此函数，必须保留它实际需要的 import。
去掉私有出口时不删异常映射行为，不影响 Chroma、SQLite、Life episode 和 scoped memory 召回。

### 3.8 执行后结果真实性缺口（P02，优先于大范围整合）

`pentest/governed_skill_runtime.py:PentestSkillExecutionPort.execute` 在 executor 抛异常时调用 `_rejected`，该函数固定返回 `side_effects_executed=False`；executor 返回 error/failed 时也固定报告 False。
但 executor 可能已产生部分效果后才失败。`pentest/secure_execution.py` 的对应路径明确用 `EffectState.UNCERTAIN`，并只在 guard 日志确证前置拒绝时使用 NOT_EXECUTED。
现有 `test_pentest_secure_execution.py` 也测试执行异常、保存失败不误报零执行；本轮没有找到统一适配器同等正向/不确定性测试。

这是源码可见的语义差异。潜在后果是调用方低估已发生效果或错误重试，但尚未证明每个下游都会据此自动重放。
P02 先用 recorder 复现并追踪既有 TaskGraph 结果合同；修复需复用现有不确定性/对账语义，不能自造另一种副作用状态或仅改一个布尔值就声称完成。

### 3.9 凭据保护与异常吞没

`autonomous_kernel.py:CredentialVault` 有可预测默认派生和 XOR fallback。
本轮符号搜索只找到类定义，未发现该类被内核实例化；因此不能把它描述成“正在保护所有生产凭据”，也不能宣称改这个类就修好了实际凭据生命周期。
必须先追踪实际 credential_ref/明文/持久化/日志路径，再决定接入受支持的凭据存储或移除无作用的误导实现。不能关闭凭据类能力来换安全测试通过。

宽泛 `except Exception: pass` 在 Tribe/Pentest 较集中，但有些是明确可选增强。逐项区分可选观察失败与授权/持久写入失败；不全局替换为抛异常，也不全局静默成功。

## 4. 全仓处置矩阵

| 范围 | 处置 | 小模型不得自行做的事 |
|---|---|---|
| app、composition、input/surface/agent runtime | 保留生产入口；局部缩短纯转发和端口重复 | 合并聊天与所有治理路径、绕过生命周期 |
| Tribe、Persona、digital_life、bond | 方法级功能迁移后收口 | 以 canonical 未调用为由删器官功能或关系规则 |
| Pentest（含 swarm/adversarial/lab/knowledge） | 保留能力；先做 89 工具及自主能力连续性表 | 删未注册能力、默认启用真实动作、另造授权体系 |
| persistence、audit migrations、contracts、natsume_contracts | 默认 KEEP；仅有证据的局部纯结构修改 | 删除 SQL/索引/旧 receipt 读取、变更哈希签名材料 |
| governor、secure_ingress、airlock、security_immune、skills | 保留统一安全/执行绑定；清理死元数据需闭包 | 认为多层校验重复而删守卫 |
| memory、retrieval、planning_search、model_runtime | 保留召回/规划/模型预算；便利导出小批处理 | 无消费者推断知识/研究能力无用、更改模型权威 |
| scheduler、runtime、service_catalog、observability | 核对活服务、定时任务与只读投影 | 从源码删 env key 却不检查部署/健康/文档消费者 |
| evolution、bio_ops、organ_ops、release | 生产入口与工具入口分开看；必要时 MOVE | 移动算删除收益、删除人工演练或回滚能力 |
| Monitor UI | 核对路由/响应字段/生成客户端 | 删除旧字段但不做后端、UI 和部署兼容闭包 |
| Edge、desktop_pet、gateway、语音与形象桥 | 当前仅静态核对；保留跨机协议 | 启动另一台 Edge、变更端口/API/硬件控制 |
| scripts、tools、configs、docs | 操作入口是消费者；历史证据与生成产物分开 | 把 release/lab 工具当无人调用即无用 |
| tests | 移植行为测试；去掉已失去对象的布局断言 | 整批删 retirement/security/contract 测试 |

这张矩阵是覆盖范围与保留边界，不代表每个模块都已获行为等价证明。未发 READY 任务卡的区域不得自动改。

## 5. 收益与完成定义

短期收益来自少量隐式包边、私有转发环和过时布局约束；中期有约 6.6k 条件尾部候选，但其中需要迁移的分析能力会增加替代代码。
任务/持久化规模大不是其全部可删的证据；当前没有可信的“再删几十万行且无功能变化”估计。
迁移与能力恢复项目应单独统计，不把新增替代代码算成失败，也不把测试少跑算成提速。

完成要求：用户需要的能力可达；未授权无效果，授权后确实可执行；批准/拒绝/超时/撤销/恢复均有证明；旧写入不会复活；部署、协议和历史数据仍受支持。
