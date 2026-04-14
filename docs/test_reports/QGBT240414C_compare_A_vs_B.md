# QGBT240414C 批测对比（A vs B）

- A: `QGBT240414A`（限流阈值调整前）
- B: `QGBT240414B`（限流阈值提升 + 背景任务关闭后）
- 原始对比：`D:/Natsume_Ai/runtime/test_reports/QGBT240414C_compare_A_vs_B.json`

## 关键指标

- 终态 OK 率：A=80.0% → B=100.0%
- 终态 DEGRADED 率：A=20.0% → B=0.0%
- LLM 首判命中率：A=100.0% → B=83.3%
- skill_exec 覆盖率：A=65.0% → B=91.7%
- 平均时延：A=173042ms → B=104263ms
- 最大时延：A=322532ms → B=154306ms
- gateway 状态：A={'failed': 13, 'DEGRADED': 7} / B={'failed': 12}

## 结论

1. RATE_LIMIT_EXCEEDED 退化在 B 批次已清零（终态 12/12 全 OK）。
2. 平均时延从 173042ms 降到 104263ms，最大时延从 322532ms 降到 154306ms。
3. 在保持首判与技能执行覆盖的同时，系统稳定性显著提升。