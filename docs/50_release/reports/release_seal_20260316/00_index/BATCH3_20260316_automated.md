# BATCH3：自动化执行结果（2026-03-16）

**执行方式**：按需自动执行；需人工项见文末。

## 观测与 WebUI API

| 端点 | 结果 |
|------|------|
| /obs/metrics | 200 |
| /health/mcp | 200 |
| /obs/requests | 404（路径以实际路由为准） |

## 合约测试（抽样）

| 范围 | 结果 |
|------|------|
| test_web_ui_contract.py | 13 passed |
| test_quality_contract.py | 10 passed |
| test_config_contract.py | 1 failed（test_t_config_01_dev_cold_start_config_is_parseable_and_health_stable，与 health/reason_code 断言或 fixture 相关） |
| test_key_profile_contract.py | 7 passed |
| **小计** | 38 passed, 1 failed |

## 集成测试（test_host_integration.py）

| 结果 |
|------|
| 81 passed（已修复 1 例：工具列表改为 GET /tools/meta-pack） |

## 其他说明

- 部分 contract 存在 collection error（test_stream_chat_contract、test_world_canon_slot_contract 等），未纳入本批。
- 部分 unit 存在 collection error，未全量跑 unit。

---

## 后续说明

全自动批测已由 **BATCH4** 完成（集成 81 + 合约 + 单元），Chat 主链路、Governor、Tools、Obs、WebUI 等均已覆盖，**无需人工执行**。可选：浏览器级 W-04/W-05、回滚演练 R-01 按需补测。
