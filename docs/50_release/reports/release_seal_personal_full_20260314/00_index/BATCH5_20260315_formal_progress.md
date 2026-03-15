# 正式封测批次 5 进度（2026-03-15）

## 当前状态

- 正式双节点封测：**进行中**
- 当前批次：**Batch 5**
- 当前总体状态：**IN PROGRESS**

## 本批次已执行

- `F-14 / F-15` 主链压缩与总结能力复盘
- `F-28` Obs / API / 字段闭环对照

## 本批次结果

| 项 | 状态 | 说明 |
|---|---|---|
| F-14 | BLOCK | `main_chain` 依赖的 `get_context_compressor()` 当前不存在，主链压缩能力未真正接线 |
| F-15 | BLOCK | 仅存在占位级 `session_summary` skill 与 `bond_summary` helper，正式总结闭环未形成 |
| F-28 | BLOCK | `/obs/chain/stats`、`/obs/input/dispatch-stats`、`/obs/context-compressor/stats` 与源表存在明确偏差 |

## 关键发现

1. `F-14` 当前不是“暂时没抓到证据”，而是主链压缩依赖未接线，属于实现级阻断。
2. `F-15` 当前没有满足“阶段总结 / 会话总结”要求的正式产物，也没有真实调用证据。
3. `F-28` 当前至少有三处 API 统计口径与源数据不一致：
   - `AUDIT_SNAPSHOT` 实际落在 `error_log`，但 `/obs/chain/stats` 读取 `audit_log`
   - 输入分发实际写入 `INPUT_*` 事件，但 `/obs/input/dispatch-stats` 未匹配这些事件名
   - `context-compressor/stats` 的统计口径与当前真实落盘闭环不一致

## 已落盘报告

- `20_chain_quality/F-09_F-14_F-15_F-16_F-17_20260315_batch4.md`
- `40_webui_obs/F-28_obs_api_field_closure_20260315_batch5.md`

## 下一步建议顺序

1. 优先修复 `F-14` 的主链压缩接线与 `F-28` 的 obs 读表/读字段口径。
2. 修复后重新复测 `F-14 / F-15 / F-28`，确认压缩、总结和观测三者闭环。
3. 继续等待 `F-06` soak 采样完成并汇总。

## 当前结论

- 当前质量与观测面仍存在多项阻断，正式封测不能放行。
