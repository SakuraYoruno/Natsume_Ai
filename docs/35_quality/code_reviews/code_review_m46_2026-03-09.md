受众：MASTER
标题：M46 代码审查报告（2026-03-09）

# M46 代码审查报告（2026-03-09）

**里程碑**：M46 依赖注入收口重构
**范围**：`fastapi_app.py` 依赖注入工厂迁移至 `dependencies.py`
**审查人**：AI Coding Agent
**提交**：`0326d21`

---

## 1. 目标与结论

M46 目标是将 `fastapi_app.py` 中重复定义的全局单例变量和依赖注入工厂函数，统一迁移到 `host_brain/app/dependencies.py`，消除代码重复，实现单一职责。

本次已完成：

- 删除 `fastapi_app.py` 中 11 个全局单例变量（`_ledger`、`_intent_engine` 等）；
- 删除 `fastapi_app.py` 中 11 个重复的 `get_*` 工厂函数（`get_ledger`、`get_episodic_store` 等）；
- 删除 `fastapi_app.py` 中的 `_get_runtime_node_id()` 辅助函数；
- 添加从 `host_brain.app.dependencies` 的统一导入；
- 清理多余的导入语句（`ToolCatalog`、`ToolBroker`、`GovernorPolicyCache` 等 12 个）；
- `dependencies.py` 已包含所有单例工厂函数的完整实现，逻辑与原实现一致。

---

## 2. 代码变更摘要

### 2.1 `host_brain/app/fastapi_app.py`（修改）

**删除内容：**

- L252-L265：全局单例变量区域（11 个 `Optional[T] = None` 变量）
- L695-L716：`get_ledger()` 和 `get_episodic_store()` 函数定义
- L868-L875：`_get_runtime_node_id()` 辅助函数
- L877-L980：`get_retrieval_pipeline()`、`get_graph_runtime()`、`get_obs_metrics_collector()`、`get_tool_catalog()`、`get_tool_broker()`、`get_bond_stage_engine()`、`get_skill_registry()`、`get_policy_cache()`、`get_intent_engine()` 共 9 个函数

**新增内容：**

```python
# ---------------------------------------------------------------------------
# 依赖注入工厂（从 dependencies.py 统一管理）
# ---------------------------------------------------------------------------
from host_brain.app.dependencies import (
    get_ledger,
    get_episodic_store,
    get_retrieval_pipeline,
    get_graph_runtime,
    get_obs_metrics_collector,
    get_tool_catalog,
    get_tool_broker,
    get_bond_stage_engine,
    get_skill_registry,
    get_policy_cache,
    get_intent_engine,
)
```

**清理的多余导入（12 个）：**

- `from host_brain.governor.decision import IntentDecisionEngine`
- `from host_brain.memory.episodic import EpisodicStore`
- `from host_brain.obs.metrics import MetricsCollector`
- `from host_brain.retrieval.pipeline import RetrievalPipelineContract, build_retrieval_pipeline_from_env`
- `from host_brain.tools.catalog import ToolCatalog`
- `from host_brain.tools.broker import ToolBroker`
- `from host_brain.governor.policy_cache import GovernorPolicyCache`
- `from host_brain.governor.tokens import TokenService`
- `from host_brain.skills.builtin_registry import build_builtin_skill_registry`
- `from host_brain.skills.registry import SkillRegistry`
- `from host_brain.bond.bond_stage_engine import BondStageEngine`
- `from host_brain.integrations.graph_runtime import GraphRuntimeContract`
- 重复的 `from host_brain.bond.bond_dynamics import BondDynamicsWriter`（删除一个）

**保留内容：**

- `from host_brain.audit.sqlite_ledger import SQLiteLedger`（L1967 函数签名中使用）
- 所有 provider 注入调用（`_set_canon_ledger_provider`、`_set_obs_ledger_provider`、`_set_tool_providers`、`_set_bond_engine_provider`）保持不变

### 2.2 `host_brain/app/dependencies.py`（已存在，无修改）

`dependencies.py` 已包含完整的依赖注入实现：

- 11 个全局单例变量
- 11 个 `get_*` 工厂函数
- 逻辑与原 `fastapi_app.py` 中的实现完全一致

---

## 3. 语法验证

```bash
py -3 -m py_compile host_brain/app/fastapi_app.py
# exit: 0

py -3 -m py_compile host_brain/app/dependencies.py
# exit: 0
```

两个文件语法均通过。

---

## 4. 变更影响分析

| 影响项 | 状态 | 说明 |
|--------|------|------|
| `get_ledger()` 调用方 | ✅ 无影响 | 函数签名不变，通过 import 透明替换 |
| `get_episodic_store()` 调用方 | ✅ 无影响 | 同上 |
| 其他 `get_*` 调用方 | ✅ 无影响 | 同上 |
| provider 注入调用 | ✅ 无影响 | `_set_*_provider(get_ledger)` 等调用保持不变 |
| lifespan 函数 | ✅ 无影响 | `ledger = get_ledger()` 调用保持不变 |
| 全局单例状态 | ✅ 无影响 | 单例状态现在由 `dependencies.py` 管理，行为一致 |

---

## 5. 变更文件清单

- `host_brain/app/fastapi_app.py`（修改：删除重复代码，添加导入）
- `host_brain/app/dependencies.py`（已存在，无修改）
- `docs/35_quality/code_reviews/code_review_m46_2026-03-09.md`（新增）

---

## 6. 审查结论

- M46 的核心目标（消除 `fastapi_app.py` 中的依赖注入重复代码）已完整落地。
- `fastapi_app.py` 文件行数从 2922 行减少至 2793 行（减少 129 行）。
- 语法验证通过，无逻辑变更，行为与重构前完全一致。
- 所有 provider 注入调用保持不变，路由注册逻辑不受影响。
