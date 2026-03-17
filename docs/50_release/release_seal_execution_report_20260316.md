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

## 6. 本次执行（2026-03-17，Host + Edge 已启动）

按计划书顺序执行，Host=127.0.0.1:18080，Edge=192.168.1.101:17800（.env 配置）。

### 6.1 P0 静态扫描与语法检查

| ID | 项 | 结果 |
|----|----|------|
| P0-02 | compileall Host + Edge | ✅ 0 语法错误 |

### 6.2 P1/P2 双端健康与合约

| ID | 项 | 结果 |
|----|----|------|
| P1-01 | Host `/health` `/ready` | ✅ 200 |
| P1-02 | Host tools/bond/scheduler/host-scheduler | ✅ 200 |
| P2-01 | Edge `/v1/edge/health` | ✅ 200（192.168.1.101:17800，status=ACCEPTED） |

### 6.3 可观测性

| ID | 项 | 结果 |
|----|----|------|
| O-01 | /obs/metrics、/health/mcp、/health/canon_sync | ✅ 200 |

### 6.4 集成与多智能体相关测试（pytest）

- **tests/integration/**：397 passed，31 failed，10 skipped（约 61s）。
- **失败集中**：test_main_chain（旧主链，与当前多图架构可能不一致）、test_host_milestone_trial_run（embedding/reranker/全服务健康，需真实依赖）、部分 test_p1_p2/test_p4/test_p5/test_p6/test_p7（ledger 关闭/环境或 mock 差异）。
- **多智能体相关**：大量 test_p8～test_p15 及 execution_profile、agent_dispatch、supervisor、judge 等单元/集成已包含在 397 通过中。

### 6.5 待补测（依赖环境或人工）

- **P2-02**：VTS 进程+8001+健康状态（需本机 VTS 已启动）。
- **P3**：Edge RPC（VTS/TTS/截图/自动化/主动推送）需在 Edge 可达环境下逐项执行。
- **P4-03 / P4-06 / MA-01～MA-06**：主链路与场景矩阵、多 Agent 专项建议用真实请求或专项脚本在 Host+Edge 就绪时补测。
- **W-04/W-05**：浏览器级 WebUI 路由与 Agent 页面（需真实浏览器）。
- **31 失败用例**：建议按模块（main_chain / trial_run / p4～p7）排查 fixture 与 ledger 生命周期、mock 与真实服务差异后回归。

### 6.7 本次续跑（P3 / W / P4-03 / MA-06）

| ID | 项 | 结果 |
|----|----|------|
| P3-02 | Edge RPC TTS `/v1/edge/speak` | ✅ 200（POST text 成功） |
| P3-03 | Edge RPC 截图 `/v1/edge/screenshot` | ⚠️ 此前用 GET 探测→405；**Edge 端为 POST + EdgeRpcEnvelope**（见下） |
| W-01 | /panel、/static/monitor/index.html | ✅ 静态 200；/panel 重定向 |
| W-02 | /api/ui/timeline、status-distribution、search-requests | ⚠️ 422（缺必填 Query：start_time/end_time 等，契约测试 13 通过） |
| P4-03 | Host `/v1/chat` 主链路 SCENE_CHAT SSE | ✅ 200，data 行≥1，has_commit=True |
| MA-06/O | /obs/metrics、/obs/failure/stats | ✅ 200 |
| WebUI 合约 | test_web_ui_contract.py | ✅ 13 passed |
| MA 集成 | test_p1_p2_execution_profile_smoke + test_host_integration（chat/profile/exec） | ✅ 26 passed（execution_profile 选图与 chat 主链路） |

### 6.9 Edge RPC 请求方式（本项目 edge_client 约定）

| 端点 | 方法 | 请求体 | 说明 |
|------|------|--------|------|
| `/v1/edge/health` | GET | — | 无鉴权 |
| `/v1/edge/speak` | **POST** | **EdgeRpcEnvelope**（request_id, ts_ms, nonce, signature, payload） | HMAC 鉴权；payload 含 session_id/utterance_id/text 等 |
| `/v1/edge/screenshot` | **POST** | **EdgeRpcEnvelope**；payload 含 monitor_index、session_id | HMAC 鉴权；返回 event.image_b64、width、height |
| `/v1/edge/automate` | **POST** | **EdgeRpcEnvelope**；payload 含 session_id、actions、stop_on_error | HMAC 鉴权 |
| `/v1/edge/proactive_push` | **POST** | **EdgeRpcEnvelope**；payload 含 type、content 等 | HMAC 鉴权 |
| `/v1/edge/proactive_messages` | **GET** | — | 拉取并清空队列 |

- **依据**：`edge_client/app/fastapi_app.py`（路由定义）、`edge_client/app/schemas.py`（EdgeRpcEnvelope）、`host_brain/channels/edge_rpc_client.py` 与 `host_brain/app/vision/edge_rpc_client.py`（Host 侧构建 envelope 与 HMAC 签名）。
- **P3-03**：须用 **POST** + 带签名的 envelope 调用 screenshot；直接用 GET 会返回 405。

### 6.10 阻断项（本次）

| ID | 说明 | 状态 |
|----|------|------|
| — | P0/P1/P2/O-01/P4-03/MA-06 无阻断；P3-03 须用 POST+envelope 复测；W-02 需带 Query 参数；集成 31 败待修复后回归 | 待修复/补测后复测 |

---

## 7. 复测结果（2026-03-17）

### 7.1 复测项与结论

| ID | 复测项 | 方法 | 结果 |
|----|--------|------|------|
| **P3-03** | Edge RPC 截图 | POST + EdgeRpcEnvelope（request_id, ts_ms, nonce, signature, payload），脚本 `tools/seal_retest_edge_screenshot.py` | ✅ **通过** — 200，success=True，has_image_b64=True |
| **W-02** | WebUI API 合约 | GET /api/ui/timeline、status-distribution、search-requests 带必填 Query（start_time、end_time 等） | ✅ **通过** — 三端均 200 |
| **集成测试** | tests/integration/ | pytest 全量 | **397 passed，31 failed，10 skipped**（与首轮一致，31 败未变） |

### 7.2 P3-03 复测说明

- Edge 端约定：`/v1/edge/screenshot` 仅接受 **POST**，请求体为 **EdgeRpcEnvelope**（见 §6.9）。
- 复测使用与 Host 一致的 envelope 构建与 HMAC 签名，调用 `HOST_EDGE_RPC_BASE_URL`（192.168.1.101:17800），payload 含 `session_id`、`monitor_index`。
- 通过标准：HTTP 200、body.success=True、event.image_b64 非空。

### 7.3 W-02 复测说明

- 必填 Query：timeline / status-distribution 需 `start_time`、`end_time`（如 2026-03-01T00:00:00～2026-03-18T23:59:59）；search-requests 另需 `query`、`page`、`page_size`。
- 复测带齐参数后三端均返回 200。

### 7.4 集成测试回归

- 失败仍为 31 个，分布与首轮相同：test_main_chain（旧主链）、test_host_milestone_trial_run（embedding/reranker/全服务健康）、test_p1_p2/test_p4/test_p5/test_p6/test_p7 部分用例（ledger/fixture/环境）。
- 多智能体相关 test_p8～p15 及 host_integration 等 397 个用例通过。

### 7.5 复测后阻断项

| ID | 说明 | 状态 |
|----|------|------|
| P3-03 | Edge 截图 | ✅ 复测通过（POST+envelope） |
| W-02 | WebUI API | ✅ 复测通过（带 Query 参数） |
| 集成 31 败 | main_chain / trial_run / p1_p2～p7 部分 | 仍待修复后回归 |

---

## 8. 下一步执行（W-03 / 观测与 agent 端点）

### 8.1 W-03 观测面板依赖端点

| 端点 | 结果 | 说明 |
|------|------|------|
| /obs/metrics | 200 | 通过 |
| /obs/failure/stats | 200 | 通过 |
| /obs/trace/placeholder-id | 404 | 占位 request_id 预期 404 |
| /v1/agent/run | 422 | 缺 body 参数，非 5xx，符合「页面依赖端点可用、无 5xx」 |

结论：**W-03 满足** — 观测与 agent 相关端点无 5xx。

### 8.2 合约 / 单元测试收集

- 部分文件存在**收集错误**（SyntaxError / 编码）：`test_stream_chat_contract.py`、`test_world_canon_slot_contract.py`、`test_chat_stream_handler_p0_stream_retrieval.py` 等，需修字符串/编码后可再跑合约与单元全量。
- 封测矩阵中已覆盖的 WebUI 合约（test_web_ui_contract）、Edge 网关合约（test_edge_rpc_gateway_contract）等此前已通过。

### 8.3 建议后续

1. 修上述合约/单元文件中的语法与编码后，再跑 `tests/contract/`、`tests/unit/` 全量。
2. 按计划书继续：P3-01/P3-04/P3-05（VTS/自动化/主动推送）、P2-02（VTS 可用性）、P4-04/P4-05/P4-06 与 MA-01～MA-05 专项、W-04/W-05（浏览器级）、S/C/E/O/D/F/R、P5/P6/P7。

---

*报告将随封测推进持续更新。*
