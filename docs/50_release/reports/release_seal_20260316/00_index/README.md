受众：MASTER
标题：封测执行目录 release_seal_20260316

# 封测执行目录 release_seal_20260316

**开始日期**：2026-03-16  
**依据**：`docs/50_release/release_seal_test_plan_20260313.md` + `release_seal_test_plan_supplement_20260315.md`  
**多智能体参考**：`docs/50_project/MULTI_AGENT_UPGRADE_PLAN_2026-03-16.md`

## 子目录

| 子目录 | 用途 |
|--------|------|
| `00_index/` | 总索引、批次说明、P0/P1/… 执行结论、最终报告 |
| `10_health_runtime/` | 健康检查、配置、依赖、F-01~F-07 等 |
| `20_chain_quality/` | Chat 主链路、召回、质量、时延、场景矩阵 |
| `30_module_matrix/` | Agent/Tools/MCP/Skills/Canon/Bond/Vision/WeChat/Scheduler 等 |
| `40_webui_obs/` | WebUI、Obs、API、Trace |
| `50_recovery_rollback/` | 备份、恢复、回滚演练 |
| `60_security_isolation/` | 鉴权、重放、隔离 |
| `70_raw_evidence/` | 原始日志、截图、录屏等 |

## 执行顺序

1. ✅ P0 静态扫描与语法检查（已完成）
2. ✅ P1/P2 双端健康与合约（BATCH2 已完成）
3. P3 Edge RPC
4. P4 主链路与场景矩阵（BATCH4 集成测试已覆盖 Chat/Stream）
5. W / S / C / E / O / D / F / R 专项（BATCH4 已自动覆盖）
6. P5 Skills / P6 审计 / P7 汇总
