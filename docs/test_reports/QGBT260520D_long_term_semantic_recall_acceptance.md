# QGBT260520D 长期记忆语义召回验收

日期：2026-05-20

## 结论

- 验收对象：`memory_search(search_scope=long_term)`。
- 结论：通过，长期记忆语义召回链路已接通。
- 运行时不再使用空 mock。
- 运行时 Chroma endpoint 已对齐到 `127.0.0.1:18000`。
- 运行时 collection 已对齐到 `episodic`。
- 检索结果已通过 `memory_layer=long_term` 过滤，不再混入最近事件或工具验收哨兵。

## 本轮发现并修复

初始验收发现运行时 `SemanticRecaller` 存在三处断链：

- `use_mock=true`，mock store 为空。
- collection 指向 `episodic_memory`，但实际运行 collection 是 `episodic`。
- Chroma 端口默认 `8000`，但实际运行端口是 `18000`。

修复后：

- `get_semantic_recaller()` 跟随 Chroma runtime config。
- 默认 collection 使用 `CHROMA_DEFAULT_COLLECTION`，当前为 `episodic`。
- 默认 endpoint 使用 `CHROMA_ENDPOINT`，当前为 `http://127.0.0.1:18000`。
- embedding 维度跟随 collection profile，当前为 `1024`。
- `long_term` 检索自动追加 `memory_layer=long_term` 过滤。

## 长期向量库重建

使用非破坏方式重建长期投影：

```powershell
python tools\rebuild_long_term_vector_db.py --keep-existing-collections --collection episodic --dim 1024
```

结果：

- `deleted_collections=[]`
- `removed_long_term_count=221`
- `inserted_count=217`
- `seed_count=217`
- `semantic_memory=0`
- `memory_ops_log=217`
- `skipped_test_or_requestless=1482`
- `surface_original_quotes` 保留，count=`8674`

本轮同时过滤掉工具验收类哨兵，避免它们进入长期记忆。

## 真实验收结果

证据目录：`artifacts/long_term_semantic_recall_acceptance/run-20260520-163128`

- `status=PASSED`
- `source=semantic_recaller`
- `search_scope=long_term`
- `result_count=5`
- `all_long_term=true`
- `contains_acceptance_sentinel=false`
- `episodic_count=755`
- `long_term_count=217`
- `surface_original_quotes_count=8674`

## 测试

```powershell
python -m pytest -q tests\integration\test_executive_skill_runtime_smoke.py::test_execute_skill_long_term_memory_search_filters_semantic_layer tests\unit\test_memory_steward_skills.py tests\unit\test_tool_execution_matrix.py tests\unit\test_rebuild_long_term_vector_db.py tests\unit\test_recall_fusion_semantic_memory.py
```

结果：`15 passed in 9.40s`

补充测试：

```powershell
python -m pytest -q tests\unit\test_rebuild_long_term_vector_db.py tests\integration\test_executive_skill_runtime_smoke.py::test_execute_skill_long_term_memory_search_filters_semantic_layer tests\unit\test_memory_steward_skills.py
```

结果：`9 passed in 0.84s`

## 剩余风险

- `semantic_memory` 表当前为 0，长期语义召回主要来自 `memory_ops_log` 投影，内容仍偏事件回放。
- 下一步应验收 / 强化长期记忆蒸馏，把重复情节压成更稳定的偏好、关系和事实摘要。
