受众：CODEX
标题：全面代码审阅报告（第六版，2026-03-03）

# 🔍 Natsume Ai 全面代码审阅报告

> **生成日期**：2026-03-03（第六版，M10-D 收口）
> **审阅范围**：M10-D 收口改动（F-41/F-06/F-35/F-36/F-37/F-39/F-12）+ 环境验证补齐（F-01/F-02/F-11）
> **审阅方法**：差异审阅 + 聚焦回归测试 + 追踪链一致性检查

---

## 一、M10-D 已完成修复（本版结论）

| 发现项 | 模块 | 修复状态 | 说明 |
|---|---|---|---|
| F-41 | `memory/distillation.py` | ✅ DONE | `distill_top_k()` 增加蒸馏级并发锁与锁内幂等复检，避免 source_hash 竞态 |
| F-06 | `audit/sqlite_ledger.py` | ✅ DONE | `_table_has_column()` 新增线程安全缓存，减少重复 PRAGMA |
| F-35 | `app/canon_routes.py` | ✅ DONE | approve 路径移除二次 DB 读取依赖，缩小非事务窗口 |
| F-36 | `app/canon_routes.py` | ✅ DONE | reject 审计写入失败升级为 500（不再 warning 吞掉） |
| F-37 | `release/manifests/m10a_release.json` | ✅ DONE | 补锁 `canon_world_store.py` / `sqlite_ledger.py` hash |
| F-39 | `memory/episodic.py` / canon proposal 链路 | ✅ DONE | `source_intent_id` 从 diary 提案提交贯通到 DB 落库与测试断言 |
| F-12 | `requirements.txt` | ✅ DONE | 明确 `redis` 为可选依赖（Optional Dependencies） |
| F-05 | 部署文档 | ✅ DONE | 明确 SQLite WAL 仅建议本地磁盘，NFS/网络盘禁止作为 Ledger 主库 |
| F-01 | `airlock/sandbox.py` | ✅ DONE | 增加生产容器硬隔离约束（`require_container`/`Natsume_ENV`），容器不可用时硬失败 |
| F-02 | `airlock/sandbox.py` | ✅ DONE | subprocess 超时路径统一为 `Popen.communicate(timeout)` + 平台感知终止策略 |
| F-11 | E2E 测试 | ✅ DONE | 新增真实 Edge RPC 入口 E2E（通过 `/v1/edge/events` 上报 ASR/TTS 事件） |

---

## 二、聚焦测试结果

本版执行聚焦回归：

- `tests/contract/test_sandbox_contract.py`
- `tests/contract/test_sandbox_docker_contract.py`
- `tests/e2e/test_wechat_vts_voice_rpc_e2e.py`
- `tests/contract/test_rpc_event_contract.py`
- `tests/contract/test_release_manifest_contract.py`
- `tests/contract/test_model_plane_release_contract.py`

结果：

```text
102 passed, 0 failed
```

---

## 三、剩余发现项（更新后）

### 3.1 P1（剩余 2 项 + 1 项 TEST_READY）

| # | 模块 | 发现 | 建议 |
|---|---|---|---|
| F-04 | `governor/rate_limit_store.py` | Redis 后端真实验证待执行（已补集成测试） | 本地单机部署可暂缓；多实例部署前执行 `NASTUME_TEST_REDIS_URL` 集成测试 |
| F-07 | Edge ASR | CPU 降级延迟（设备实测） | 真机性能验证 |
| F-08 | Edge TTS+ASR | GPU VRAM OOM（压力测试） | 真机压力与资源门闸验证 |

### 3.2 P2（剩余 10 项）

F-13、F-14、F-15、F-16、F-18、F-19、F-20、F-25、F-26、F-27。

---

## 四、本版结论

M10-D 收口完成后，P1 代码级可修复项已清空。剩余 P1 主要集中在部署/设备验证（Redis 真环境可选验证 + Edge 真机实测）。

建议下一批次进入“设备验证包”：`F-07/F-08`；`F-04` 在单机本地部署可按需延后。
