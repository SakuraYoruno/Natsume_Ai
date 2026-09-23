# P02：Pentest 执行后效果真相审计

审计日期：2026-09-09
状态：**HOLD（需要通用 Capability 结果契约的设计决策）**
本卡性质：证据审计；本回执没有修改任何 Pentest 执行、授权或风险策略。

## 范围与安全边界

- 仅静态读取源码、运行元数据检查和运行一个会因缺少依赖而跳过的
  RECORDING_ONLY 集成测试。
- 没有启动扫描、网络请求、进程、Docker、WSL、真实 executor 或 SecureRun HTTP
  接口。
- 没有伪造 _AUTHORITY_SEAL、LabCapability 或 Governor/TaskGraph authority。

## 已证实的两条执行路径

~~~
统一 Skill / TaskGraph 路径
  FrozenSkillWorker
    -> execute_governed_skill
    -> dispatch_executor_payload
    -> CapabilityBroker
    -> PentestSkillExecutionPort
    -> PentestSecureRunComposition.executors[tool_id]

SecureRun HTTP 路径
  secure ingress / durable receipt
    -> PentestSecureRunComposition.execution_service
    -> SecurePentestExecutionService
    -> PentestSecureRunComposition.executors[tool_id]
    -> SQLitePentestRunStore.tool_results[].effect_state
~~~

两条路径复用了同一份 server-owned executor map，但不是同一条效果结果与持久化链。
这不证明统一 Skill 路径绕过了授权：它仍检查 sealed
BoundSkillExecutionAuthority、通用 CapabilityGrant、高风险 review:// 引用、
LabCapability、scope 和 rate guard。缺口在于**执行器开始后出现异常时，效果状态的
表达与持久化不一致**。

源码证据：

- host_brain/pentest/production_runtime.py:80,84,186-187,211：一个
  PentestSecureRunComposition 同时持有 execution_service 和 executors。
- host_brain/app/tool_runtime_composition.py:176-182,228：通用运行时把该
  composition 交给 PentestSkillExecutionPort。
- host_brain/app/pentest_secure_run_routes.py:306-325：SecureRun HTTP 路径调用
  composition.execution_service.execute(...)。

## 发现：异常后的状态被降格

### 统一 Skill 路径

host_brain/pentest/governed_skill_runtime.py：

- 187-194：executor 抛出任意异常时，捕获后返回
  PENTEST_SKILL_EXECUTOR_FAILED。
- _rejected(...) 的固定输出（36-48）包含 side_effects_executed=false。
- 196-212：executor 正常返回但带 error、blocked、denied、failed 或 rejected 状态时，
  也固定返回 side_effects_executed=false。

因此，即使 executor 已经进入了 LabCapability 上下文、做过部分工作再抛异常，统一路径
仍把结果投影为“未发生副作用”。该返回是普通 mapping，不是异常。

CapabilityBroker 会把“适配器正常返回 mapping”封存为成功调用：

- host_brain/capability_plane/broker.py:342-390：adapter 返回则创建
  ReceiptStatus.SUCCEEDED；只有 adapter 抛出、超时、租约/日志异常等情形会走失败或
  IN_DOUBT。
- host_brain/skills/executor_registry.py 的 broker adapter 正是把 Pentest port 的
  mapping 原样作为 adapter 输出。

随后 frozen worker 只将 Skill outcome 压缩成 SUCCESS 或 FAILED：

- host_brain/app/governed_frozen_skill_runtime.py:512-542：
  record_governed_skill_execution(...) 只写 result_status/result_code，公开 COMMIT 也只
  给出 APPROVE 或 REJECT。

所以同一次 executor 异常可能同时得到：

~~~
Capability receipt: SUCCEEDED（adapter 正常返回一个拒绝 mapping）
TaskGraph Skill result: FAILED
Effect truth: 没有字段，且布尔值被写为 false
~~~

这不是可安全重试的结论。

### SecureRun HTTP 路径

host_brain/pentest/secure_execution.py 已有正确的三态模型：

- 66-86：EffectState = NOT_EXECUTED | EXECUTED | UNCERTAIN；
  UNCERTAIN 被视为可能已经发生效果。
- 239-247：executor 抛异常后设为 UNCERTAIN。
- 249-277：非明确 guard 拒绝的错误结果也设为 UNCERTAIN；
  只有 guard 可证明“尚未调用效果”才保留 NOT_EXECUTED。
- 300-321：结果落库失败或非 NEW CAS 结果同样为 UNCERTAIN。
- 396-431：将 effect_state 写入 PentestRun.tool_results[]。

host_brain/app/pentest_secure_run_routes.py:552-557,703-720 会将 UNCERTAIN
返回为 503，并要求后续对账，而不是把请求完成为普通失败。
现有安全测试已覆盖这两个关键事实：
tests/security/test_pentest_secure_execution.py:435-476。

## 可复用的现有通用语义

不需要为 Pentest 新建第二套 Skill 或审批体系：

- host_brain/capability_plane/contracts.py 已有 ReceiptStatus.IN_DOUBT。
- host_brain/capability_plane/broker.py:212,245,256,279,287,361,412 已在日志、
  超时和遗留 claim 情况下使用 IN_DOUBT 并禁止盲目重放。
- host_brain/task_runtime/operation_recovery_runtime.py:183-188 已有
  side_effect_state: UNKNOWN 与 required_signed_reconciliation_actions，表达
  “不可自动重试，必须对账”。

但是当前通用 Skill adapter 没有一条“executor 已进入效果边界、结果未知”的泛化出口，
所以不能直接复用这些语义而不改契约。

## 不应做的改动

- 不删除 SecurePentestExecutionService、SecureRun HTTP 路由、LabCapability、
  Pentest executor map 或任一 Pentest Skill。
- 不把统一 Skill worker 反向调用 SecureRun HTTP；那会引入第二次 ingress/HMAC/run
  创建和另一套 authority，而不是融合。
- 不在 governed_skill_runtime.py 中把异常继续标成 side_effects_executed=false，也不以
  “测试容易通过”为由吞掉异常。
- 不根据这个回执自动把 AUTHORIZED_LAB 打开。

## 建议的最小后续设计（尚未实施）

1. 在**通用** CapabilityBroker 结果契约中增加“adapter 已进入效果边界、结果未知”的
   正式信号，并映射到已有 ReceiptStatus.IN_DOUBT。该信号不能是 Pentest 专用类型。
2. Pentest adapter 只在以下情况发出该通用未知信号：executor 已在
   LabCapability/ExecutorGuard 内开始后抛异常、返回非 guard 可证明的错误，或结果
   持久化未知。LabIsolationDenied 和 guard 已证明的调用前拒绝仍保持 NOT_EXECUTED。
3. dispatch_executor_payload、execute_governed_skill 和 frozen worker 必须把
   IN_DOUBT 投影为现有的 side_effect_state: UNKNOWN，携带 capability receipt 引用，
   并禁止普通 retry；只能进入已有的签名对账/恢复路径。
4. 等该通用结果契约完成后，再评估是否能让 SecureRun service 与统一 Skill adapter
   共享一个内部“效果结果记录器”。不得删除外部 SecureRun API，直到其调用方完成迁移。

预计会触及通用边界而非 Pentest 功能：

- host_brain/capability_plane/contracts.py
- host_brain/capability_plane/broker.py
- host_brain/skills/executor_registry.py
- host_brain/pentest/governed_skill_runtime.py
- host_brain/app/governed_skill_execution.py
- host_brain/app/governed_frozen_skill_runtime.py
- 对应 contract/integration/security 测试

这超过 P02 的“证据审计”授权，必须先完成契约与回放语义设计，再实施。

## 运行证据与本机限制

新增了 tests/integration/test_cleanup_pentest_skill_continuity.py，它使用正式
SkillRegistry（注册 89 个 Pentest Skills）、正式 authority factory、真实
CapabilityBroker 和 SQLite journal，但底层只有 RECORDING_ONLY recorder。测试要求
高风险工具先有 review://，并设计为验证第一次记录、持久回执 replay 不重复调用和
target drift 拒绝。

本机 Python 缺少项目 requirements 中声明的 cryptography：

~~~
cryptography_available=False
ModuleNotFoundError: No module named 'cryptography'
~~~

因为 canonical effect-argument digest 依赖该包，测试在测试体内显式 skip：

~~~
1 skipped
Pentest secure-run canonical effect digest requires cryptography
~~~

没有为本机测试伪造 digest、seal、grant 或安装依赖；CI 安装 requirements 后必须运行该
测试及既有 tests/security/test_pentest_secure_execution.py。
