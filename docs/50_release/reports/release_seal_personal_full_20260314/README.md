# 封测测试产物目录

**所有封测测试产物均在此目录下**，即项目中的 `docs/50_release/reports/release_seal_personal_full_20260314/`。  
本目录由 **Git 管理**，与封测计划 7.1 一致。

与封测计划 7.1 一致，子目录分工如下：

| 子目录 | 用途 |
|--------|------|
| `00_index/` | 总索引、执行批次说明（BATCH4/BATCH5）、最终结论、需人工项说明、微信实测说明 |
| `10_health_runtime/` | 健康检查、配置、依赖、重启恢复、**F-06 长稳 soak** 采样（RSS、health） |
| `20_chain_quality/` | Chat 主链路、召回、质量样本、**F-17 时延**、**F-09/F-16 复测结论** |
| `30_module_matrix/` | Agent、Tools、MCP、Skills、Canon、Bond、Vision、WeChat、Scheduler、Distillation 等模块级证据 |
| `40_webui_obs/` | `/panel`、`/api/ui/*`、`/obs/*`、截图、控制台、HAR、字段映射 |
| `50_recovery_rollback/` | 备份、恢复、最小回滚、恢复后验证 |
| `60_security_isolation/` | 鉴权、重放、rate-limit、未授权调用、单用户多会话隔离 |
| `70_raw_evidence/` | 原始日志、trace、录屏、音频、截图和临时导出工件 |

命名约定：文件名前缀优先使用 `F-xx_`，便于回溯到封测计划中的阻断项。
