# QGBT240414O - 自动规划新子智能体（Capability Forge Auto Planner）落地留痕

## 目标
把“能力缺口 -> 临时能力补位”扩展为“能力缺口 -> 自动生成长期能力提案（Forge Proposal）”，并保留治理门控。

## 本次实现
1. 在 `capability_forge` 增加自动规划入口 `maybe_auto_plan_for_gap(...)`：
   - 按候选阈值自动判定是否生成提案。
   - 同候选去重（已有 `PENDING_APPROVAL/APPROVED` 不重复提案）。
   - 支持回滚后冷却时间。
   - 支持可选自动审批（默认关闭）。
2. 在 `executive_runtime_core` 缺口链路接入自动规划：
   - 在 `chat -> capability gap` 命中后，自动尝试触发 Forge 提案。
   - 执行结果写入 `runtime_capability_payload.capability_forge.auto_plan` 便于追踪。
3. 补充 `.env.example` 配置项：
   - `HOST_CAPABILITY_FORGE_AUTO_PROPOSAL_ENABLED`
   - `HOST_CAPABILITY_FORGE_AUTO_MIN_DETECTED`
   - `HOST_CAPABILITY_FORGE_AUTO_MIN_SESSIONS`
   - `HOST_CAPABILITY_FORGE_AUTO_MIN_FAILURE_RATE`
   - `HOST_CAPABILITY_FORGE_AUTO_MIN_FAILED`
   - `HOST_CAPABILITY_FORGE_AUTO_DEDUPE_COOLDOWN_MS`
   - `HOST_CAPABILITY_FORGE_AUTO_CANARY_TRAFFIC_PERCENT`
   - `HOST_CAPABILITY_FORGE_AUTO_APPROVE_ENABLED`
   - `HOST_CAPABILITY_FORGE_AUTO_APPROVE_STAGE`
   - `HOST_CAPABILITY_FORGE_AUTO_APPROVER`

## 关键行为说明
- 默认行为：自动“创建提案”，不自动审批，仍由治理流程决定是否上线。
- 可选行为：打开自动审批后，可自动推进到 `shadow/canary/full`（建议 `shadow` 或 `canary`）。

## 验证命令与结果
1. `pytest tests/unit/test_capability_forge_service.py tests/unit/test_runtime_capability_synthesizer.py -q`
   - 结果：`11 passed`
2. `pytest tests/unit/test_executive_runtime_core_auto_forge.py tests/unit/test_capability_forge_service.py -q`
   - 结果：`9 passed`
3. `pytest tests/unit/test_runtime_capability_synthesizer.py tests/unit/test_executive_runtime_entry.py tests/unit/test_executive_runtime_helpers.py tests/unit/test_executive_runtime_core_auto_forge.py tests/unit/test_capability_forge_service.py -q`
   - 结果：`19 passed`

## 变更文件
- `host_brain/app/capability_forge.py`
- `host_brain/app/executive_runtime_core.py`
- `.env.example`
- `tests/unit/test_capability_forge_service.py`
- `tests/unit/test_executive_runtime_core_auto_forge.py`

## 结论
“自动规划新的子智能体功能”已从仅有人工 Forge 操作升级为：运行时自动发现缺口并自动生成长期能力提案，且保持治理可控。
