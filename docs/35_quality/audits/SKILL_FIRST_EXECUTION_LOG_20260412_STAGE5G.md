受众：CODEX
标题：Skill-First 执行留痕（P5G-Unified Risk Bar）日期：2026-04-12

# Skill-First 执行留痕（P5G-Unified Risk Bar）日期：2026-04-12
批次：`CC#18`
状态：`COMPLETED`

## 1. 目标
- 上线跨页统一 Forge 风险条，并提供一键审批/回滚入口。

## 2. 代码变更
- `host_brain/app/monitor_ui/src/api/obs.js`
- `host_brain/app/monitor_ui/src/components/ForgeRiskActionBar.jsx`
- `host_brain/app/monitor_ui/src/components/Layout.jsx`
- `docs/50_project/arch/SKILL_FIRST_HUMANOID_EXECUTION_PLAN_STAGE5G_UNIFIED_RISK_BAR_20260412.md`
- `docs/35_quality/audits/SKILL_FIRST_EXECUTION_LOG_20260412_STAGE5G.md`

## 3. 执行命令与结果
```powershell
npm run build
```
- cwd: `host_brain/app/monitor_ui`
- 结果：`PASS`

```powershell
pytest tests/unit/test_obs_capability_gaps_stats.py tests/unit/test_obs_capability_forge_proposals.py -q
```
- 结果：`3 passed`

## 4. 验证结论
- 所有页面顶部统一显示 Forge 风险条（风险存在时）。
- 风险条可直接触发 Forge 审批到 Canary 与手动回滚。
- 钻取链路（Alerts/Insights）与运行时质量链路保持一致。
