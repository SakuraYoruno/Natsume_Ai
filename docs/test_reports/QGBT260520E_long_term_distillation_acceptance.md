# QGBT260520E 长期记忆蒸馏闭环验收

## 结论

状态：PASSED

本批验收修复并验证了长期记忆蒸馏链路：

```text
Chroma 长期记忆投影
  -> EpisodicStore.recall()
  -> long_term_memory_distillation
  -> semantic_memory
  -> semantic_slot
  -> semantic_memory recall route
```

修复前，真实账本中 `semantic_memory` 为 0，且新进程启动后的 `EpisodicStore._records` 为空；旧实现会把 Chroma 召回结果按 `_records` 过滤掉，导致蒸馏技能看见“无情节记忆”。

修复后，`EpisodicStore.recall()` 会保留持久化 Chroma 返回的记录；如果当前进程内有同一条 `_records`，仍继续叠加记忆衰减、压制、失真等人性化权重。

## 真实验收

验收产物：

```text
artifacts/long_term_distillation_acceptance/run-20260520-164024/long_term_distillation_acceptance.json
```

关键结果：

| 项目 | 结果 |
| --- | --- |
| 验收状态 | PASSED |
| 验收查询 | 用户日常习惯与偏好 |
| 验收前 semantic_memory | 0 |
| 新进程内 `_records` | 0 |
| Chroma 持久化召回数量 | 5 |
| `long_term_memory_distillation` 状态 | OK |
| topics_processed | 1 |
| semantic_memories_created | 1 |
| proposals_submitted | 1 |
| errors | 0 |
| 验收后 semantic_memory | 1 |
| semantic_slot_count | 1 |
| semantic recall candidates | 1 |
| 长期向量投影 seed_count | 218 |
| 长期向量投影 semantic_memory | 1 |

最新语义记忆：

```text
semantic_id=sem_cd09064680444a259b1f810844147bde
source_count=5
distill_strategy=merge_top_k
confidence=0.4839
tags=long_term_distill, 用户日常习惯与偏好
```

长期向量投影刷新：

```powershell
python tools\rebuild_long_term_vector_db.py --keep-existing-collections --collection episodic --dim 1024
```

结果：

```text
removed_long_term_count=217
seed_count=218
inserted_count=218
seed_stats.semantic_memory=1
seed_stats.memory_ops_log=217
skipped_test_or_requestless=1482
```

刷新后用 `memory_layer=long_term` 过滤进行向量召回，新的语义记忆已进入长期向量层：

```text
doc_id=semantic::sem_cd09064680444a259b1f810844147bde
source_table=semantic_memory
score=0.8229
rank=1
```

## 回归测试

命令：

```powershell
python -m pytest -q tests\unit\test_persistent_episodic_store.py tests\unit\test_memory_distillation.py tests\unit\test_long_term_distiller.py tests\unit\test_distiller_emotion.py
python -m pytest -q tests\unit\test_persistent_episodic_store.py tests\unit\test_memory_distillation.py tests\unit\test_long_term_distiller.py tests\unit\test_distiller_emotion.py tests\unit\test_tool_execution_matrix.py tests\unit\test_memory_steward_skills.py tests\unit\test_recall_fusion_semantic_memory.py tests\integration\test_executive_skill_runtime_smoke.py::test_execute_skill_long_term_memory_search_filters_semantic_layer
```

结果：

```text
60 passed in 9.87s
73 passed in 18.44s
```

新增覆盖：

- `EpisodicStore.recall()` 在 `_records` 为空、Chroma 有持久化行时仍返回记忆。
- `MemoryDistillationEngine.distill_top_k()` 在 `_records` 为空、Chroma 有持久化行时仍能写入 `semantic_memory`。

## 后续关注

这批验收打通了“经历沉淀为长期语义记忆”的闭环。下一项建议继续验收 `long_term_memory_distillation` 的调度入口和 proposal/canon 后续审核链，确认长期语义记忆不仅能生成，还能进入世界观/人格规范层。
