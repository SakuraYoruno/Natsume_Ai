# Skill-First 类人体架构执行补充方案（P6E-Runtime Helper Migration）日期：2026-04-12
状态：`COMPLETED`

## 1. 本轮目标
- 将 `dispatch_legacy_core` 内部的可复用执行辅助逻辑迁入 `executive_runtime`，减少 legacy 函数体耦合。
- 为后续 P6F 的“主逻辑逐段迁出”提前完成工具层迁移。

## 2. 已迁移能力
- `dispatch handoff` 记录与事件发布：
  - 新位置：`executive_runtime.record_dispatch_handoff`
  - legacy core 改为调用 runtime helper。
- `runtime capability handoff extra` 计算：
  - 新位置：`executive_runtime.build_runtime_capability_handoff_extra`
  - legacy core 改为调用 runtime helper。
- `runtime capability execution` 结果上报：
  - 新位置：`executive_runtime.record_runtime_capability_execution`
  - legacy core 改为调用 runtime helper。

## 3. 结构收益
- `dispatch_agent` 内部主函数长度与职责复杂度下降（辅助逻辑外置）。
- `executive_runtime` 从“单纯桥接层”升级为“拥有可复用执行能力的运行时层”。
- 后续迁移主流程时，只需迁移路由编排与执行分支，不必重复搬运遥测/留痕工具代码。

## 4. 测试补强
- 新增 `tests/unit/test_executive_runtime_helpers.py`，覆盖：
  - runtime capability handoff extra 结构输出；
  - forge + gap telemetry 的执行上报行为；
  - dispatch handoff 记录与 tribe 事件发布行为。

## 5. 下一步（P6F）
- 将 `dispatch_legacy_core` 中的 route/plan/delegate/skill 分支逐段迁入 `executive_runtime`。
- 完成后下线 `dispatch_agent._dispatch_agent_executive_core_legacy_impl`。

