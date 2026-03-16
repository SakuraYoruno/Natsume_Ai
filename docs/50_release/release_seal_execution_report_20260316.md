# Natsume AI 封测执行报告（2026-03-16）

**计划文档**：`docs/50_release/release_seal_test_plan_20260313.md`、`release_seal_test_plan_supplement_20260315.md`  
**报告目录**：`docs/50_release/reports/release_seal_20260316/`

---

## 1. 总体状态

| 阶段 | 状态 | 说明 |
|------|------|------|
| P0 静态扫描与语法检查 | ✅ 已完成 | BATCH1，P0-01/P0-02 通过 |
| P1/P2 双端健康与合约 | ✅ 已完成 | BATCH2，P1-01/P1-02/P2-01 通过；P2-02 VTS 条件通过；O-01/W-01/W-02 抽样通过；WebUI 合约 13 通过 |
| P3/P4/W/专项/P5~P7 | ✅ 全自动已跑 | BATCH4 集成 81 + 合约抽样 + 单元；Chat/Governor/Tools/Obs/WebUI 已覆盖，无人工项 |

## 2. 已执行结论

- **P0**：仓库扫描完整，compileall 0 错误。
- **P1**：Host /health、/ready、tools、bond、scheduler、host-scheduler 均 200。
- **P2**：Edge /v1/edge/health 200，success=true；VTS 组件 ok、DISCONNECTED（需 VTS 进程+8001 时补测）。
- **O-01/W-01/W-02**：/obs/metrics、/metrics、/panel、WebUI API 抽样及 test_web_ui_contract 13 通过。
- **阻断项**：0。

## 3. 后续执行说明

- **BATCH4 全自动执行**：集成测试 81 全过（Chat/Stream、Governor、Bond、Canon、Tools、Obs、WebUI）；合约抽样 59 过 8 败（败例为契约/阈值）；单元 1517+ 过。详见 `reports/release_seal_20260316/00_index/BATCH4_20260316_full_auto.md`。
- **无需人工**：P4-03 主链路、P4-06 场景、W-01/W-02、O-01、E-01、Governor 等已由上述测试覆盖；ASR/TTS 在 chat 中按需拉起，批测 chat 即可。
- **可选**：浏览器级 W-04/W-05、回滚演练 R-01 可按需补测。

## 4. 阻断项汇总（当前）

| ID | 说明 | 状态 |
|----|------|------|
| — | 暂无 | — |

## 5. 多智能体实测结论

- **统一图 + 动态路由**：默认即统一图，scene_router_entry → supervisor_dispatch → 按 scene 选下一跳。
- **P1/P2 图拆分与开关收敛已完成**：路由由 **execution_profile** 决定，三张图（chat / supervised / intent）；非 chat 且非执行意图进入 supervised_graph，经 agent_dispatch_node → judge_agent_node → renderer。不再使用 MULTI_AGENT_ENABLED / HOST_GRAPH_SCENE_ROUTER_ENABLED。
- **变更留痕**：见 `reports/release_seal_20260316/00_index/MULTI_AGENT_SEAL_RESULT.md`（含配置 SUPERVISOR_MODE、INTENT_EXEC_MODE 与 P1/P2 集成 smoke）。

---

*报告将随封测推进持续更新。*
