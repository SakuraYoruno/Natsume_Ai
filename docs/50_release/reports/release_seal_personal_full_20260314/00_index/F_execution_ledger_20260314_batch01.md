# F-01 ~ F-03 执行台账（batch-01）

## 1. 执行元信息

| 项 | 填写 |
|---|---|
| batch_id | 20260314_batch01_health_gate |
| exec_date | 2026-03-14 |
| operator | Codex + user runtime observation pending |
| host_env | historical bootstrap diagnosis only, invalid for formal LAN dual-node seal test |
| edge_env | historical bootstrap diagnosis only, invalid for formal LAN dual-node seal test |
| vts_env | localhost:8001, not connected |
| enabled_modules | host, edge, retrieval, scheduler, bond, tools, voice_chain |
| n_a_modules | none in this batch |
| start_time | 2026-03-14 23:46 CST |
| end_time | 2026-03-14 23:52 CST |

## 2. 汇总

| 项 | 数值 |
|---|---|
| applicable_count | 3 |
| pass_count | 0 |
| block_count | 3 |
| n_a_count | 0 |
| final_decision | BLOCK（preflight only） |

## 3. 执行总表

| ID | scope | status | system_evidence | desktop_evidence | blocker_summary | retest_status |
|---|---|---|---|---|---|---|
| F-01 | Host/Edge/VTS 健康 | BLOCK | `10_health_runtime/F-01_health_summary.md` `10_health_runtime/F-01_health_snapshot.json` | blocked_before_desktop_observation | `edge` health is `degraded`; `tts=UNLOADED`; `vts=DISCONNECTED`; `host-scheduler.running=false` | pending |
| F-02 | 启动、配置与密钥 | BLOCK | `10_health_runtime/F-02_config_keys.md` `10_health_runtime/F-02_config_snapshot.json` | not_applicable_this_batch | startup config drift requires local overrides; scheduler registration warning remains | pending |
| F-03 | 外部依赖就绪 | BLOCK | `10_health_runtime/F-03_dependency_readiness.md` `10_health_runtime/F-03_dependency_probe.json` | not_applicable_this_batch | infra restored, but semantic recall still defaults to mock; `models/gpt_sovits` missing; `pyvts` absent; VTS executable not found | pending |

## 4. 阻断项汇总

| ID | issue | severity | fix_owner | fix_plan | retest_due |
|---|---|---|---|---|---|
| F-01 | Edge voice chain degraded | high | runtime | load TTS runtime, connect VTS, confirm host-scheduler running state | next batch |
| F-02 | Config drift and startup warnings | high | runtime/code | consolidate local sealtest overrides and clear scheduler registration error | next batch |
| F-03 | Retrieval and voice dependencies not fully ready | high | runtime/code | disable semantic recall mock, provision `models/gpt_sovits`, install `pyvts`, restore VTube Studio executable/startup, then re-run voice readiness | next batch |
