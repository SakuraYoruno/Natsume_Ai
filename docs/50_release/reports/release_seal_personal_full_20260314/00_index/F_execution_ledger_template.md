# F-01 ~ F-33 执行台账模板

适用目录：`docs/50_release/reports/release_seal_personal_full_20260314/`

## 1. 执行元信息

| 项 | 填写 |
|---|---|
| batch_id |  |
| exec_date |  |
| operator |  |
| host_env |  |
| edge_env |  |
| vts_env |  |
| enabled_modules |  |
| n_a_modules |  |
| start_time |  |
| end_time |  |

## 2. 执行规则

1. `status` 只允许填写：`PASS`、`BLOCK`、`N/A`。
2. `N/A` 只能用于明确未启用功能，且必须写明原因。
3. 正式封测只接受局域网双节点；若 `Edge/VTS 节点` 不可达，本轮直接 `BLOCK`。
4. 涉及白盒观测的项，`desktop_evidence` 不能为空。
5. `system_evidence` 与 `desktop_evidence` 均填写相对路径，默认相对 `docs/50_release/reports/release_seal_personal_full_20260314/`。
6. 若存在阻断项，`blocker_summary` 与 `retest_status` 不得留空。

## 3. 汇总

| 项 | 数值 |
|---|---|
| applicable_count |  |
| pass_count |  |
| block_count |  |
| n_a_count |  |
| final_decision |  |

## 4. 执行总表

| ID | scope | status | system_evidence | desktop_evidence | blocker_summary | retest_status |
|---|---|---|---|---|---|---|
| F-01 | Host/Edge/VTS 健康 |  | `10_health_runtime/F-01_*` |  |  |  |
| F-02 | 启动、配置与密钥 |  | `10_health_runtime/F-02_*` |  |  |  |
| F-03 | 外部依赖就绪 |  | `10_health_runtime/F-03_*` |  |  |  |
| F-04 | 单机高可用与重启恢复 |  | `10_health_runtime/F-04_*` |  |  |  |
| F-05 | 备份、恢复与最小回滚 |  | `50_recovery_rollback/F-05_*` |  |  |  |
| F-06 | 长稳与资源泄漏 |  | `10_health_runtime/F-06_*` |  |  |  |
| F-07 | 失败语义与恢复动作 |  | `10_health_runtime/F-07_*` |  |  |  |
| F-08 | Chat 主链路 |  | `20_chain_quality/F-08_*` |  |  |  |
| F-09 | 七路召回准确性 |  | `20_chain_quality/F-09_*` |  |  |  |
| F-10 | 情感信号全链路传递 |  | `20_chain_quality/F-10_*` | `20_chain_quality/F-10_*` |  |  |
| F-11 | 情感值驱动 VTS 表情 |  | `20_chain_quality/F-11_*` | `20_chain_quality/F-11_*` |  |  |
| F-12 | TTS -> VTS 口型同步 |  | `20_chain_quality/F-12_*` | `20_chain_quality/F-12_*` |  |  |
| F-13 | Edge 事件回补链路 |  | `20_chain_quality/F-13_*` |  |  |  |
| F-14 | 上下文压缩回注主链路 |  | `20_chain_quality/F-14_*` |  |  |  |
| F-15 | 总结质量 |  | `20_chain_quality/F-15_*` |  |  |  |
| F-16 | 文本生成质量 |  | `20_chain_quality/F-16_*` |  |  |  |
| F-17 | Chat 主链路耗时 |  | `20_chain_quality/F-17_*` |  |  |  |
| F-18 | 常用场景矩阵 |  | `20_chain_quality/F-18_*` | `20_chain_quality/F-18_*` |  |  |
| F-19 | Agent / Checkpoint 生命周期 |  | `30_module_matrix/F-19_*` |  |  |  |
| F-20 | Tools / MCP / 常用 Skills |  | `30_module_matrix/F-20_*` |  |  |  |
| F-21 | Canon 热词与 Canon 审批 |  | `30_module_matrix/F-21_*` |  |  |  |
| F-22 | Bond Summary 注入 |  | `30_module_matrix/F-22_*` |  |  |  |
| F-23 | Vision 截图与自动化 |  | `30_module_matrix/F-23_*` | `30_module_matrix/F-23_*` |  |  |
| F-24 | WeChat 链路 |  | `30_module_matrix/F-24_*` |  |  |  |
| F-25 | Scheduler / 主动推送 |  | `30_module_matrix/F-25_*` |  |  |  |
| F-26 | Distillation / Vault / Semantic Recall |  | `30_module_matrix/F-26_*` |  |  |  |
| F-27 | WebUI 全页面闭环 |  | `40_webui_obs/F-27_*` | `40_webui_obs/F-27_*` |  |  |
| F-28 | Obs / API / 字段闭环 |  | `40_webui_obs/F-28_*` | `40_webui_obs/F-28_*` |  |  |
| F-29 | 审计与 Trace 闭环 |  | `40_webui_obs/F-29_*` | `40_webui_obs/F-29_*` |  |  |
| F-30 | 基础安全与误调用防护 |  | `60_security_isolation/F-30_*` |  |  |  |
| F-31 | Provider / Fallback 可用性 |  | `60_security_isolation/F-31_*` |  |  |  |
| F-32 | 单用户多会话隔离 |  | `60_security_isolation/F-32_*` |  |  |  |
| F-33 | 已启用端点覆盖完整性 |  | `60_security_isolation/F-33_*` |  |  |  |

## 5. 阻断项汇总

| ID | issue | severity | fix_owner | fix_plan | retest_due |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

## 6. 额外记录

### 6.1 N/A 说明

| ID | reason |
|---|---|
|  |  |

### 6.2 电脑侧白盒观察摘要

| ID | observation |
|---|---|
| F-10 |  |
| F-11 |  |
| F-12 |  |
| F-18 |  |
| F-23 |  |
| F-27 |  |
| F-28 |  |
| F-29 |  |
