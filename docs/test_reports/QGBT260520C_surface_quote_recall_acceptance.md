# QGBT260520C 台词 / 风格语料召回验收

日期：2026-05-20

## 结论

- 验收对象：`surface_original_quotes` 台词 / 风格语料召回。
- 结论：通过，当前技能可用。
- Chroma collection：`surface_original_quotes`
- collection_count：`8674`
- 查询模型：`models/Qwen3-Embedding-0.6B`

## 验收范围

本次验收覆盖 11 个场景：

- 可用强台词场景：`short_continue`、`negative_feedback`、`pushed_away_boundary`、`return_chat`、`lover_presence`、`ambiguous_affection`
- 策略性禁用 / 待样本场景：`tired`、`technical_next_step`、`explicit_memory`、`relationship_memory_reassurance`、`daily_morning`

该技能的正确目标不是“所有场景都塞台词”，而是：

- 强台词场景能召回可用中文原句。
- 任务、照看、记忆确认、早安等高误喂风险场景保持禁用或待样本。
- 输出 guidance 明确标注台词只是风格样本，不是当前事实。

## 真实 Review 结果

证据目录：`artifacts/surface_original_quote_recall_review/run-20260520-160506`

- `case_count=11`
- `selected_total=15`
- `selected_max=4`
- `selected_min=0`
- `degraded_count=5`
- degraded case ids：
  - `tired`
  - `technical_next_step`
  - `explicit_memory`
  - `relationship_memory_reassurance`
  - `daily_morning`

这些 degraded 均来自当前策略的 `SURFACE_ORIGINAL_QUOTES_NEED_SAMPLES`，属于预期保护，不是 Chroma 断链。

## 场景结果

| case | policy | raw candidates | selected | result |
|---|---:|---:|---:|---|
| `short_continue` | strong | 60 | 3 | 通过 |
| `negative_feedback_customer_service` | strong | 86 | 4 | 通过 |
| `pushed_away_boundary` | strong | 49 | 1 | 通过但覆盖偏薄 |
| `return_chat` | strong | 48 | 3 | 通过 |
| `lover_presence` | strong | 66 | 3 | 通过 |
| `ambiguous_affection` | strong | 58 | 1 | 通过 |
| `tired` | need_samples | 0 | 0 | 预期禁用 |
| `technical_next_step` | need_samples | 0 | 0 | 预期禁用 |
| `explicit_memory` | need_samples | 0 | 0 | 预期禁用 |
| `relationship_memory_reassurance` | need_samples | 0 | 0 | 预期禁用 |
| `daily_morning` | need_samples | 0 | 0 | 预期禁用 |

## 单测

```powershell
python -m pytest -q tests\unit\test_surface_original_quote_bank.py tests\unit\test_surface_quote_recall_review.py
```

结果：`27 passed in 0.88s`

## 后续建议

- 为 `pushed_away_boundary` 增补更多“被推开但仍保留照看出口”的人工认可样本。
- `tired` / `technical_next_step` / `explicit_memory` / `relationship_memory_reassurance` 不建议直接放开，需要先做 curated samples，否则容易把剧情、关系或客服腔错误喂入回复。
