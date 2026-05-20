# QGBT260520G Canon 提案可审核闭环验收

## 结论

状态：PASSED

本批验收修复了长期记忆蒸馏进入 Canon 后的“可审核性”断点：

```text
long_term_memory_distillation
  -> canon_proposal_log pending
  -> API/前端可读取 proposed_title/proposed_tags/proposed_body
  -> approve route 可直接使用已存内容
```

修复前，`canon_proposal_log` 只保存 `proposed_hash` 和 `summary`，不保存 title/tags/body；但审核 API 又需要提交完整内容。结果是前端能看到“有 pending proposal”，却拿不到可审核正文。

修复后：

- `canon_proposal_log` 新增 `proposed_title`、`proposed_tags`、`proposed_body`。
- `WorldCanonStore.submit_proposal()` 会持久化完整可审核内容。
- `CanonProposalResponse` 会返回完整可审核内容。
- `approve_proposal` 在未显式传正文时，会使用 proposal 中保存的内容。
- `LongTermMemoryDistiller.backfill_pending_proposal_content()` 可补齐旧版 pending 蒸馏提案。

## 真实验收

验收产物：

```text
artifacts/canon_proposal_content_acceptance/run-20260520-165939/canon_proposal_content_acceptance.json
```

真实账本回填结果：

| 项目 | 回填前 | 回填后 |
| --- | ---: | ---: |
| pending distill proposals | 1 | 1 |
| reviewable proposals | 0 | 1 |

回填结果：

```text
updated=1
skipped=0
errors=0
```

已验证 proposal：

```text
proposal_id=distill_prop_sem_cd0906468044_bb4d0729
status=pending
has_title=true
tag_count=3
has_body=true
summary=蒸馏主题='用户日常习惯与偏好' semantic_id=sem_cd09064680444a259b1f810844147bde source_count=5
```

API 验收：

```text
list_proposals(status='pending') 返回该 distill proposal
has_title=true
tag_count=3
has_body=true
status=pending
```

注意：本次只补齐可审核内容，没有自动 approve，也没有写入 World Canon SSOT；最终采纳仍应由用户或治理流程确认。

## 回归测试

命令：

```powershell
python -m pytest -q tests\unit\test_canon_proposal_content.py tests\unit\test_canon_routes_tx.py tests\unit\test_canon_cursor_pagination.py tests\unit\test_canon_hotwords.py tests\unit\test_long_term_distiller.py tests\unit\test_memory_distillation.py
python -m pytest -q tests\unit\test_canon_proposal_content.py tests\unit\test_canon_routes_tx.py tests\unit\test_canon_cursor_pagination.py tests\unit\test_canon_hotwords.py tests\unit\test_long_term_distiller.py tests\unit\test_memory_distillation.py tests\unit\test_persistent_episodic_store.py tests\unit\test_tool_execution_matrix.py tests\unit\test_memory_steward_skills.py tests\unit\test_recall_fusion_semantic_memory.py tests\integration\test_executive_skill_runtime_smoke.py::test_execute_skill_long_term_memory_search_filters_semantic_layer
```

结果：

```text
73 passed in 10.25s
96 passed in 19.77s
```
