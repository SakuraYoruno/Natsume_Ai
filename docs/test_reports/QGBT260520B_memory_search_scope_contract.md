# QGBT260520B 记忆检索边界契约

日期：2026-05-20

## 结论

- 已将 `memory_search` 从“默认语义优先”改为显式检索域契约。
- 最近事件、刚写入内容、工具闭环默认走 `recent_episode`。
- 长期记忆必须显式走 `long_term`。
- 台词 / 风格语料必须显式走 `line`。
- 混合检索必须显式走 `hybrid`，不再作为隐式默认。

## 契约

`memory_search.search_scope` 支持：

- `recent_episode`：最近事件、工具闭环、任务事件、会话事件，使用 EpisodicStore。
- `long_term`：用户偏好、关系记忆、长期事实，使用语义召回。
- `line`：台词、风格、对白语料，使用语义召回。
- `hybrid`：显式混合模式，先语义，语义为空时再回 recent episode。

自动推断规则：

- `memory_type=episodic` 或 tags 含 `task/event/conversation/reminder/dream/diary` -> `recent_episode`
- `memory_type=semantic` 或 tags 含 `fact/preference/bond/emotion` -> `long_term`
- tags 含 `quote/line/dialogue/style` -> `line`
- 未指定且无法推断 -> `recent_episode`

## 修复内容

- `host_brain/skills/memory_registry.py`
  - 新增 `search_scope` 归一化与别名。
  - 扩充记忆标签：`quote/line/dialogue/style`。
  - 更新 `memory_search` 描述，明确检索域。

- `host_brain/skills/impl/memory_executor.py`
  - `recent_episode` 只走 EpisodicStore。
  - `long_term` / `line` 只走 semantic_recaller。
  - `hybrid` 显式允许语义为空后回落到 EpisodicStore。
  - 返回结果中带出 `search_scope`，便于验收和观测。

- `host_brain/app/tool_execution_matrix.py`
  - 工具闭环验收显式使用 `memory_type=episodic` + `search_scope=recent_episode`。
  - 验收摘要记录 `source` 和 `search_scope`。

## 复测结果

单测：

```powershell
python -m pytest -q tests\unit\test_memory_steward_skills.py tests\unit\test_tool_execution_matrix.py tests\unit\test_episodic_vector_dim_fallback.py
```

结果：`13 passed in 9.13s`

真实闭环：

- 证据目录：`artifacts/tool_execution_acceptance/run-20260520-154729`
- 文件：`memory_scope_closed_loop.json`
- `status=PASSED`
- `recalled=true`
- `result_count=1`
- `source=episodic_store`
- `search_scope=recent_episode`
