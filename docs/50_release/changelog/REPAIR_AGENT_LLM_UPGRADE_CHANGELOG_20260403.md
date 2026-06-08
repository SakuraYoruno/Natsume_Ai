受众：PROJECT
标题：CHANGELOG — RepairAgent 自愈机制 LLM 升级

# CHANGELOG — RepairAgent 自愈机制 LLM 升级

> **日期**：2026-04-03
> **类型**：Feature Enhancement
> **影响范围**：`host_brain/app/handlers/repair_agent_handler.py`

---

## 变更摘要

将 RepairAgent 的修复决策从**硬编码规则**升级为 **LLM 驱动的通用修复规划器**，同时新增安全门控机制。

---

## 变更详情

### 新增

- **`_REPAIR_PLANNER_SYSTEM_PROMPT`**：LLM 修复规划器的 System Prompt，约束输出格式和安全边界
- **`_is_safe_command(cmd)`**：安全门控函数，拦截危险命令（rm -rf、format、shutdown 等）
- **`_call_repair_planner_llm(task_text, previous_error)`**：调用 LLM 生成修复计划，失败时返回 None 触发降级
- **`_execute_repair_plan(plan)`**：执行 LLM 生成的修复计划，含安全门控
- **`_DANGEROUS_KEYWORDS`**：危险命令关键词黑名单（frozenset）
- **新增环境变量**：
  - `REPAIR_AGENT_LLM_ENABLED`（默认 `true`）
  - `REPAIR_AGENT_LLM_PROVIDER`（默认 `gemini`）
  - `REPAIR_AGENT_LLM_MODEL_ID`（默认 `gemini-2.0-flash`）

### 修改

- **`_build_repair_result()`**：从单一规则函数改为三层优先级调度器
  - 层1：浏览器错误 → 规则快速路径（保留原逻辑）
  - 层2：LLM 规划器（新增，通用修复）
  - 层3：规则兜底（原逻辑降级）

### 新增测试（`tests/unit/test_repair_agent_handler.py`）

- `test_build_repair_result_uses_llm_planner_for_unknown_error`
- `test_build_repair_result_llm_planner_disabled_falls_back`
- `test_build_repair_result_llm_planner_returns_none_falls_back`
- `test_is_safe_command_blocks_dangerous_commands`
- `test_execute_repair_plan_blocks_dangerous_command`

### 保留不变

- `_repair_browser_backend()` — 浏览器修复逻辑
- `repair_agent_handler()` — 主处理器
- 所有审计链字段

---

## 向后兼容性

✅ 完全向后兼容：
- 原有浏览器修复快速路径保留
- 原有 2 个测试用例全部通过
- `REPAIR_AGENT_LLM_ENABLED=false` 可完全禁用 LLM 路径，回退到原行为

---

# CHANGELOG — RepairAgent 自愈机制 持续优化（Round 2）

> **日期**：2026-04-03
> **类型**：Enhancement / Bug Fix
> **影响范围**：`host_brain/app/handlers/repair_agent_handler.py`、`tests/unit/test_repair_agent_handler.py`

---

## 变更摘要

在 LLM 规划器基础上，新增**修复历史缓存**、**部分成功重试策略**和 **`can_retry` 字段透传**，进一步缩短自愈延迟并提升重试决策精度。

---

## 变更详情

### 新增

- **`_repair_plan_cache`**：进程级修复计划缓存字典，结构为 `{error_fingerprint: (plan_or_None, timestamp_s)}`
- **`_REPAIR_CACHE_TTL_S = 300`**：缓存 TTL，5 分钟内相同错误指纹复用上次 LLM 结果
- **`_get_error_fingerprint(previous_error)`**：提取错误指纹（前 120 字符小写），用于缓存 key
- **`repair_agent_trace.can_retry`**：`repair_agent_handler` 返回值中新增 `can_retry` 字段
- **`repair_agent_trace.all_success`**：区分全部成功与部分成功

### 修改

- **`_build_repair_result()`**：LLM 规划器路径新增缓存命中检查，命中时跳过 LLM 调用；调用后立即写缓存（含 None）
- **`_execute_repair_plan()`**：`success` 判断从"全部成功"改为"部分成功"策略
  - `ok_actions > 0` → `success=True`（允许重试）
  - 全部失败/全部被安全门控拦截 → `success=False`，`can_retry=False`
  - 新增 `all_success` 字段区分全部成功与部分成功

### 新增测试（`tests/unit/test_repair_agent_handler.py`）

- `test_call_repair_planner_llm_uses_cache_on_second_call` — 验证相同错误第二次不调用 LLM
- `test_execute_repair_plan_partial_success_allows_retry` — 验证部分成功时 `success=True`、`all_success=False`
- `test_repair_agent_handler_trace_contains_can_retry` — 验证 `repair_agent_trace` 包含 `can_retry` 和 `all_success`

---

## 测试结果

```
10 passed in 0.41s  ✅
```

## 向后兼容性

✅ 完全向后兼容：
- 缓存默认启用，无需配置
- `can_retry` / `all_success` 为新增字段，不影响现有读取 `success` 的代码
- 部分成功策略仅影响 LLM 规划器路径，浏览器快速路径不变

---

# CHANGELOG — RepairAgent 自愈机制 闭环修复（Round 3）

> **日期**：2026-04-03
> **类型**：Bug Fix（关键闭环缺口）
> **影响范围**：`host_brain/app/sub_agent_protocol.py`、`tests/unit/test_sub_agent_protocol_context.py`

---

## 变更摘要

修复 `SubAgentDispatcher` 中 `repair_agent` 的 `success` 判断逻辑缺陷：原来用 `bool(final_answer)` 判断，导致修复失败时（有 summary 文本但 `can_retry=False`）也会触发重试，`can_retry` 字段形同虚设。

---

## 根因分析

```
repair_agent_handler 返回
  └─ repair_agent_trace.can_retry = False   ← 修复失败，不允许重试
  └─ react_scratchpad.final_answer = "所有修复步骤均失败..."  ← 非空字符串

SubAgentDispatcher.dispatch()
  └─ success = bool(final_answer)  ← ❌ True！（因为 final_answer 非空）

dispatch_agent.py:1265
  └─ if recovery_ctx.success and retry_original  ← ❌ 错误触发重试
```

---

## 变更详情

### 修改（`host_brain/app/sub_agent_protocol.py`）

- **`SubAgentDispatcher.dispatch()`**：当 `req.agent_id == AGENT_REPAIR` 时，从 `result["repair_agent_trace"]["can_retry"]` 读取真实的重试意图作为 `success`，而不是 `bool(final_answer)`
  - 降级顺序：`can_retry` → `repair_agent_trace.success` → `bool(final_answer)`
  - 其他所有 agent 的 `success` 判断逻辑不变

### 新增测试（`tests/unit/test_sub_agent_protocol_context.py`）

- `test_dispatcher_repair_agent_success_uses_can_retry_true` — 验证 `can_retry=True` 时 `SubAgentResponse.success=True`
- `test_dispatcher_repair_agent_success_uses_can_retry_false` — 验证 `can_retry=False` 时 `SubAgentResponse.success=False`，即使 `final_answer` 非空

---

## 测试结果

```
13 passed in 0.49s  ✅
```

## 自愈闭环完整性评估

修复后，完整的自愈信号链路为：

```
repair_agent_handler
  └─ repair_agent_trace.can_retry = True/False
       ↓
SubAgentDispatcher.dispatch()
  └─ SubAgentResponse.success = can_retry  ✅ 正确读取
       ↓
_dispatch_to_sub_agent()
  └─ SubAgentContext.success = sub_resp.success  ✅ 正确透传
       ↓
dispatch_agent.py:1265
  └─ if recovery_ctx.success and retry_original  ✅ 正确触发重试
```

**至此自愈闭环完整打通。**

## 向后兼容性

✅ 完全向后兼容：
- 仅 `repair_agent` 的 `success` 判断逻辑变更，其他所有 agent 不受影响
- 降级链保证：`repair_agent_trace` 不存在时回退到 `bool(final_answer)`，不会破坏现有行为
