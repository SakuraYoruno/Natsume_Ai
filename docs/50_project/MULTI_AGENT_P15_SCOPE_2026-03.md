# P15 SCOPE：终点封板（Supervisor LLM 提案 + graph_executor 动态路由）

**日期**：2026-03-17
**阶段**：P15（第十二轮正式基线）
**前序基线**：P14（152 用例全通过）
**定位**：多智能体规划重构升级的终点封板

---

## 1. 背景与目标

P14 封板后，M-MA6 的核心目标（所有专家 Agent ReAct 化）已全部达成。
升级计划（v1.3）中还有两项遗漏项未落地：

| 遗漏项 | 来源 | 说明 |
|--------|------|------|
| `graph_executor._dispatch_to_agent()` | M-MA6-FIX §13.3 | 根据 `active_agent_id` 动态路由到对应 handler |
| `SUPERVISOR_AUTO_ENABLED` 开关 | M-MA6 §12.6 | 升级计划中的标准开关名，与 `SUPERVISOR_MODE=llm` 等价 |

P15 以这两项为终点，完成多智能体规划重构升级的最后一块拼图。

---

## 2. 验收标准

| 编号 | 验收标准 |
|------|----------|
| 3.1 | `_dispatch_to_agent()` 可调用，根据 `active_agent_id` 返回正确 handler_id；`_AGENT_HANDLER_MAP` 包含 6 个 agent |
| 3.2 | `agent_dispatch_node` 路由正确：有 handler 时执行并同步 `agent_artifacts`，无 handler 时降级写 `DEGRADED` trace；agent handler 写的 `__next_node__` 被忽略（安全约束） |
| 3.3 | `SUPERVISOR_AUTO_ENABLED=true` 触发 LLM 模式；LLM 失败时回退 rule；与 `SUPERVISOR_MODE=llm` 等价 |
| 3.4 | P14 基线（152 用例）不退化 |

---

## 3. 改动清单

### 修改文件

| 文件路径 | 改动说明 |
|---------|----------|
| `host_brain/integrations/graph_executor.py` | 新增 `_AGENT_HANDLER_MAP`（6 个 agent）；新增 `_dispatch_to_agent()` 方法；`_run_single_step` 中新增 `agent_dispatch_node` 动态路由分支（含安全约束：pop `__next_node__` 后再用 `edge_map` 决定下一跳） |
| `host_brain/app/handlers/supervisor_dispatch_handler.py` | 补充 `SUPERVISOR_AUTO_ENABLED` 别名开关（`SUPERVISOR_AUTO_ENABLED=true` 等价于 `SUPERVISOR_MODE=llm`，优先级：`SUPERVISOR_MODE=llm` > `SUPERVISOR_AUTO_ENABLED=true` > 默认 rule） |

### 新增文件

| 文件路径 | 说明 |
|---------|------|
| `tests/unit/test_p15_dispatch_to_agent.py` | P15 单元测试（43 用例） |
| `tests/integration/test_p15_integration.py` | P15 集成测试（25 用例） |
| `docs/50_project/MULTI_AGENT_P15_SCOPE_2026-03.md` | 本文件 |
| `docs/50_project/MULTI_AGENT_P15_SEAL_RESULT_2026-03.md` | 封板结果文档 |

---

## 4. _AGENT_HANDLER_MAP 契约（P15 起）

```python
_AGENT_HANDLER_MAP: dict[str, str] = {
    "chat_agent":      "chat_agent",
    "code_agent":      "code_agent",
    "translate_agent": "translate_agent",
    "intent_agent":    "intent_agent",
    "judge_agent":     "judge_agent",
    "retrieval_agent": "retrieval_agent",
}
```

---

## 5. SUPERVISOR_AUTO_ENABLED 开关契约（P15 起）

| 环境变量 | 值 | 效果 |
|---------|-----|------|
| `SUPERVISOR_MODE` | `llm` | 触发 LLM 提案模式（最高优先级） |
| `SUPERVISOR_AUTO_ENABLED` | `true` / `1` / `yes` | 触发 LLM 提案模式（别名，与 `SUPERVISOR_MODE=llm` 等价） |
| 默认 | 未设置 | 规则路由（rule 模式） |

LLM 失败时统一回退 rule，`supervisor_decision_source` 为 `"llm_fallback_rule"`。

---

## 6. agent_dispatch_node 安全约束（P15 起）

- agent handler 写入的 `__next_node__` 会被 **强制 pop**，不影响路由
- 下一跳由 `edge_map["agent_dispatch_node"]` 决定（trusted handler 专属权）
- 未知 `active_agent_id` 时降级：写入 `DEGRADED` trace，跳转 `edge_map` 下一跳
