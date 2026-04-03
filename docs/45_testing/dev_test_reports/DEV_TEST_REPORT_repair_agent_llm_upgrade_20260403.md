# DEV TEST REPORT — RepairAgent 自愈机制 LLM 升级

> **日期**：2026-04-03
> **版本**：v1.0
> **作者**：AI 辅助开发
> **涉及文件**：
> - `host_brain/app/handlers/repair_agent_handler.py`
> - `tests/unit/test_repair_agent_handler.py`

---

## 一、背景与问题

### 1.1 原有实现的核心缺陷

原 `_build_repair_result()` 是一个**纯规则函数**，只有一个 `if` 分支：

```python
# 修改前（硬编码规则）
def _build_repair_result(*, task_text, previous_error):
    if 'BROWSER_UNAVAILABLE' in normalized_error or ...:
        return _repair_browser_backend()
    return {'success': False, 'summary': '这次故障类型还没有接入自动修复器。', ...}
```

**问题**：除浏览器问题外，所有其他失败（pip 包缺失、npm 包缺失、系统工具缺失等）都直接返回"不支持自动修复"，没有任何实际修复动作。

### 1.2 与 OpenClaw 自愈机制的差距

| 维度 | 修改前 | OpenClaw | 修改后 |
|------|--------|----------|--------|
| 修复目标 | 仅浏览器后端 | 任意依赖 | 任意依赖（LLM 推断） |
| 修复决策 | 硬编码 `if/elif` | LLM 驱动 | LLM 驱动 + 规则快速路径 |
| 可扩展性 | ❌ 需改代码 | ✅ 自动推断 | ✅ 自动推断 |
| 安全门控 | ❌ 无 | ❌ 无 | ✅ 危险命令黑名单 |
| 审计链 | ✅ 完整 | ❌ 无 | ✅ 完整（保留） |

---

## 二、设计方案

### 2.1 三层优先级架构

```
错误输入
  │
  ├─ 浏览器错误（BROWSER_UNAVAILABLE / PLAYWRIGHT / AGENT-BROWSER）
  │    └─ 快速路径：_repair_browser_backend()  ← 规则，无需 LLM，速度快
  │
  ├─ REPAIR_AGENT_LLM_ENABLED=true（默认）
  │    └─ LLM 规划器：_call_repair_planner_llm()
  │         ├─ 成功 → _execute_repair_plan()  ← 安全门控 → 执行命令
  │         └─ 失败（API Key 缺失/超时）→ 降级到规则兜底
  │
  └─ 规则兜底：返回 success=False，提示手动检查
```

### 2.2 LLM 规划器设计

**System Prompt 约束**：
- 只输出 JSON，格式固定（`repair_type` / `commands` / `summary` / `can_retry`）
- 不生成危险命令（rm -rf、format、shutdown 等）
- 命令必须是可直接执行的列表形式

**安全门控（`_is_safe_command`）**：
即使 LLM 生成了危险命令，也会被客户端侧黑名单拦截，返回 `BLOCKED_BY_SAFETY_GATE`。

**降级策略**：
- API Key 缺失 → 降级到规则兜底，记录 WARNING 日志
- LLM 调用超时/异常 → 降级到规则兜底，记录 WARNING 日志
- LLM 返回非 JSON → 降级到规则兜底

### 2.3 新增环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `REPAIR_AGENT_LLM_ENABLED` | `true` | 是否启用 LLM 规划器 |
| `REPAIR_AGENT_LLM_PROVIDER` | `gemini` | LLM 提供商（openai/gemini/deepseek） |
| `REPAIR_AGENT_LLM_MODEL_ID` | `gemini-2.0-flash` | 模型 ID（优先级低于 HOST_CHAT_AGENT_MODEL_ID） |

---

## 三、实现细节

### 3.1 新增函数

| 函数 | 职责 |
|------|------|
| `_is_safe_command(cmd)` | 安全门控：检查命令是否包含危险关键词 |
| `_call_repair_planner_llm(task_text, previous_error)` | 调用 LLM 生成修复计划，失败返回 None |
| `_execute_repair_plan(plan)` | 执行 LLM 生成的修复计划，含安全门控 |

### 3.2 修改函数

`_build_repair_result()` — 从单一规则函数改为三层优先级调度器。

`_build_failure_recovery_plan()` in `dispatch_agent.py` — 兜底分支从 `return None` 改为路由到 `repair_agent`，带 `retry_original=True`，使 LLM 规划器能处理所有未知错误码并触发自动重试。

### 3.3 保留不变

- `_repair_browser_backend()` — 浏览器修复逻辑完全保留，作为快速路径
- `repair_agent_handler()` — 主处理器逻辑不变
- 所有审计链字段（`repair_agent_trace` / `react_scratchpad` / `agent_artifacts`）不变

---

## 四、测试结果

### 4.1 测试命令

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_repair_agent_handler.py -v
```

### 4.2 测试结果

```
collected 7 items

test_repair_browser_backend_attempts_install_and_succeeds  PASSED
test_repair_agent_handler_returns_success_summary          PASSED
test_build_repair_result_uses_llm_planner_for_unknown_error  PASSED  ← 新增
test_build_repair_result_llm_planner_disabled_falls_back   PASSED  ← 新增
test_build_repair_result_llm_planner_returns_none_falls_back  PASSED  ← 新增
test_is_safe_command_blocks_dangerous_commands             PASSED  ← 新增
test_execute_repair_plan_blocks_dangerous_command          PASSED  ← 新增

7 passed in 0.59s
```

### 4.3 新增测试覆盖点

| 测试用例 | 覆盖场景 |
|----------|----------|
| `test_build_repair_result_uses_llm_planner_for_unknown_error` | LLM 规划器被调用，命令被执行，success=True |
| `test_build_repair_result_llm_planner_disabled_falls_back` | `LLM_ENABLED=false` 时降级到规则兜底 |
| `test_build_repair_result_llm_planner_returns_none_falls_back` | LLM 返回 None 时降级到规则兜底 |
| `test_is_safe_command_blocks_dangerous_commands` | 安全门控正确拦截危险命令 |
| `test_execute_repair_plan_blocks_dangerous_command` | 执行计划时危险命令被 BLOCKED_BY_SAFETY_GATE |

---

## 五、风险与注意事项

1. **LLM 延迟**：LLM 规划器超时设为 15s，不影响主链（RepairAgent 本身是异步子 Agent）
2. **API Key 依赖**：若无 API Key，自动降级到规则兜底，不影响可用性
3. **命令执行安全**：双重保障（LLM System Prompt 约束 + 客户端黑名单门控）
4. **向后兼容**：浏览器修复快速路径完全保留，原有测试全部通过

---

## 六、后续扩展方向

- [ ] 支持 `pip_install` 快速路径（常见包缺失无需 LLM）
- [ ] 修复成功后自动触发原任务重试（`retry_original=True` 信号传递给 dispatch_agent）
- [ ] 修复历史缓存（相同错误码不重复调用 LLM）
