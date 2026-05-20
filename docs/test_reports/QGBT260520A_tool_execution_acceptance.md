# QGBT260520A 工具执行验收

日期：2026-05-20

## 结论

- 工具执行矩阵：通过。
- 真实工具探针：15 / 15 通过，0 失败，0 关键失败。
- 记忆工具闭环：已从失败修复为通过。
- 闭环路径：`memory_store` 写入 -> `memory_search` 检索 -> 回读到同一条哨兵内容。

## 本轮发现

初始真实闭环验收中，`memory_store` 写入成功，`memory_search` 调用成功，但搜索结果为空：

- 证据目录：`artifacts/tool_execution_acceptance/run-20260520-152533`
- 失败点：`memory_tool_closed_loop.json`
- 现象：`store_success=true`、`search_success=true`、`recalled=false`、`result_count=0`

根因是记忆搜索执行器只要语义召回器存在就直接返回语义结果，即使结果为空，也不会继续查刚写入的 EpisodicStore。同时，搜索默认把运行时 `session_id` 传给语义召回过滤，但旧 episodic 写入没有把 session/source/tag 投影到向量元数据，导致过滤契约不完整。

## 修复内容

- `host_brain/skills/impl/memory_executor.py`
  - 语义召回返回空结果时，继续降级到 EpisodicStore。
  - 支持 EpisodicStore 的 `search()` 与 `recall()` 两种接口。
  - 统一把 `text` / `content`、`memory_id` / `doc_id` 归一化，保证上层验收能稳定判断回读。
  - 仅当 `session_filter=true` 时才把 `session_id` 传入语义过滤，避免默认搜索被隐式收窄。

- `host_brain/memory/episodic.py`
  - 从 `evidence_refs` 中提取 `request_id`、`session_id`、`source`、`tags`，同步写入向量元数据。
  - 让后续显式 session 过滤具备真实可匹配字段。

## 复测结果

复测证据目录：`artifacts/tool_execution_acceptance/run-20260520-153350`

- `tool_execution_matrix.json`
  - `status=PASSED`
  - `total=15`
  - `passed=15`
  - `failed=0`
  - `critical_failed=0`

- `memory_tool_closed_loop.json`
  - `status=PASSED`
  - `store.success=true`
  - `search.success=true`
  - `search.recalled=true`
  - `search.result_count=1`
  - 回读来源：`episodic_store`
  - 回读集合：`episodic`

## 测试

```powershell
python -m pytest -q tests\unit\test_memory_steward_skills.py tests\unit\test_episodic_vector_dim_fallback.py tests\unit\test_tool_execution_matrix.py
```

结果：`11 passed in 9.47s`
