# QGBT260520F 长期记忆蒸馏幂等与防自循环验收

## 结论

状态：PASSED

本批验收补上长期记忆蒸馏的防自循环保护：

```text
semantic_memory
  -> 长期向量投影 semantic::...
  -> 再次蒸馏同主题
```

修复前，新的 `semantic_memory` 同步进长期向量层后，下一轮同主题蒸馏可能先召回 `semantic::...`，把已蒸馏摘要再次作为源记忆，形成长期记忆回音室。

修复后，`MemoryDistillationEngine.distill_top_k()` 会跳过以下长期向量投影：

- `memory_id` 以 `semantic::` 开头
- `metadata.source_table == "semantic_memory"`
- `metadata.semantic_id` 非空

蒸馏只从原始经历/审计记忆继续取源。

## 真实验收

验收产物：

```text
artifacts/long_term_distillation_idempotency_acceptance/run-20260520-164846/long_term_distillation_idempotency_acceptance.json
```

关键结果：

| 项目 | 验收前 | 验收后 |
| --- | ---: | ---: |
| active semantic_memory | 1 | 1 |
| pending distill proposals | 1 | 1 |

同主题再次蒸馏结果：

```text
topics_processed=1
semantic_memories_created=0
proposals_submitted=0
proposals_skipped=1
errors=0
```

## 回归测试

命令：

```powershell
python -m pytest -q tests\unit\test_memory_distillation.py tests\unit\test_persistent_episodic_store.py tests\unit\test_long_term_distiller.py
python -m pytest -q tests\unit\test_memory_distillation.py tests\unit\test_persistent_episodic_store.py tests\unit\test_long_term_distiller.py tests\unit\test_tool_execution_matrix.py tests\unit\test_memory_steward_skills.py tests\unit\test_recall_fusion_semantic_memory.py tests\integration\test_executive_skill_runtime_smoke.py::test_execute_skill_long_term_memory_search_filters_semantic_layer
```

结果：

```text
57 passed in 9.54s
70 passed in 18.43s
```

新增覆盖：

- 当 Chroma 召回结果中已有 `semantic::sem_existing` 时，蒸馏会跳过它。
- 即使 `semantic::...` 排在最前，最终写入 `semantic_memory.source_memory_ids` 的也只能是原始审计/经历源。
