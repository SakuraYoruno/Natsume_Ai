# 🔍 Nastume Ai 全面代码审阅报告

> **生成日期**：2026-03-03（第四版，含 TASK-0000~0120 / M1~M10-A 全部交付）
> **审阅范围**：host_brain/、edge_client/、tests/、configs/、docs/
> **审阅方法**：逐模块源码阅读 + 契约测试全量运行 + 架构一致性检查 + 不变量静态扫描 + 安全审计

---

## 📊 一、项目总览

| 指标 | 数据 | 较上次变化（vs 第三版） |
|------|------|-----------|
| **Host Brain 源文件** | 94 个 `.py`（含 `__init__`） | +16（M8~M10-A 新增） |
| **Edge Client 源文件** | 26 个 `.py` | +9 |
| **Host Brain 代码行** | 26,536 行 | +4,210 |
| **Edge Client 代码行** | 6,816 行 | +1,446 |
| **测试代码行** | 24,175 行 | +6,426 |
| **总代码行** | **57,527 行** | +12,082 |
| **契约测试文件** | 120 个（含 integration/e2e） | +31 |
| **测试断言点** | **1041 通过 / 0 失败** ✅ | +344 通过 / **-7 失败**（全部修复） |
| **测试 Fixture 文件** | 257 个 `.json` | +49 |
| **审计日志表** | 26 张 | — |
| **错误码（reason_code）** | 148 个常量 | +4（M10-A 新增） |
| **SQL 迁移脚本** | 19 个（含 bond cypher） | +2（M9 新增 0015/0016） |
| **Git 提交**（全 TASK） | TASK-0000~0120 全部完成 | — |

---

## 🏗️ 二、架构全景（M10-A 更新）

```
┌─────────────────────── Host Brain（Python 3.12+） ─────────────────────┐
│                                                                        │
│  ┌─ app/ ──────────────────────────────────────────────────────────┐   │
│  │  fastapi_app.py      ← HTTP 入口 + 中间件 + 路由注册            │   │
│  │  main.py             ← 启动报告 + 健康聚合 + 9 节点拓扑         │   │
│  │  config.py           ← 分层配置 + 脱敏快照 + diff              │   │
│  │  web_ui.py           ← 可观测面板 API                          │   │
│  │  canon_routes.py ← NEW（M10-A）Canon 治理 REST API（7 端点）   │   │
│  │  static/panel.html   ← 链路可视化面板                          │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                        │
│  ┌─ memory/ ─────────────────────────────────────────────────────┐     │
│  │  canon_world_store.py ← NEW（M9）World Canon SSOT + 提案流程  │     │
│  │    + reject_proposal() ← NEW（M10-A）                         │     │
│  │  canon_style_store.py                                          │     │
│  │  episodic.py / chroma_client.py                                │     │
│  └───────────────────────────────────────────────────────────────┘     │
│                                                                        │
│  ┌─ audit/ ──────────────────────────────────────────────────────┐     │
│  │  sqlite_ledger.py（1,620L）← +list_proposals()（M10-A）       │     │
│  │  migration_runner.py / retention.py / drill.py                 │     │
│  │  migrations/0015_world_canon_doc.sql ← NEW（M9）              │     │
│  │  migrations/0016_canon_proposal_log.sql ← NEW（M9）           │     │
│  └───────────────────────────────────────────────────────────────┘     │
│                                                                        │
│  + governor/ + integrations/ + airlock/ + channels/ + bond/            │
│  + retrieval/ + skills/ + tools/ + lsp/ + release/ + core/ + obs/      │
└────────────────────────────────────────────────────────────────────────┘

┌─────────────────────── Edge Client ────────────────────────────────────┐
│  rpc/voice.py | rpc/event_reporter.py | rpc/resource_gate.py           │
│  senses/asr.py（+设备恢复）| senses/fallback_llm.py                    │
│  actuators/speech.py | actuators/tts_engine.py                         │
│  actuators/vts_driver.py（+指数退避重连）                               │
│  proofs/resource_gate.py | config.py                                   │
└────────────────────────────────────────────────────────────────────────┘

Canon 治理 REST API（M10-A 新增）：
  GET  /canon/proposals                    — 列出提案（分页）
  POST /canon/proposals                    — 提交新提案
  GET  /canon/proposals/{proposal_id}      — 查询提案详情
  POST /canon/proposals/{proposal_id}/approve — 审核通过
  POST /canon/proposals/{proposal_id}/reject  — 拒绝提案
  GET  /canon/documents                    — 列出 SSOT 快照
  GET  /canon/documents/{doc_id}           — 查询单个文档
```

---

## 🔬 三、M8~M10-A 新增模块审查

### 3.1 M8 运行时集成闭环（TASK-0107~0110）

| 模块 | 变化 | 评价 |
|------|------|------|
| `core/services.py` | ServiceRegistry 健康聚合 + 9 节点拓扑 | ✅ 完整 |
| `obs/metrics.py` | MetricsCollector + 延迟基线快照 | ✅ 完整 |
| `release/release_manifest.py` | generate_m8_manifest + generate_m9_manifest + generate_m10a_manifest | ✅ 完整 |
| `scripts/runtime/start_host_side.ps1` | M8/M9 健康探测 + canon_sync 钩子 | ✅ 完整 |

### 3.2 M9 World Canon 闭环（TASK-0111~0115）

| 模块 | 变化 | 评价 |
|------|------|------|
| `memory/canon_world_store.py` | WorldCanonStore（SSOT + 版本化 + 提案流程） | ✅ 完整 |
| `audit/migrations/0015_world_canon_doc.sql` | world_canon_doc 表 | ✅ 完整 |
| `audit/migrations/0016_canon_proposal_log.sql` | canon_proposal_log 表 | ✅ 完整 |
| `canon/world/*.json` | 3 个种子条目（wc-001/002/003） | ✅ 完整 |
| `core/qa.py` | QA 拦截 Canon-as-fact + 重写/降级 | ✅ 完整 |
| `memory/canon_sync.py` | 索引同步 + 幂等性 | ✅ 完整 |

### 3.3 M10-A Canon 治理 API（TASK-0117~0120）— ⭐⭐⭐⭐⭐

| 文件 | 行数 | 完成度 | 变化 |
|------|------|--------|------|
| `app/canon_routes.py` | ~350 | 95% | **NEW（M10-A）** |
| `memory/canon_world_store.py` | ~380 | 97% | +reject_proposal() |
| `audit/sqlite_ledger.py` | ~1,620 | 96% | +list_proposals() |
| `core/errors.py` | ~155 | 97% | +4 个 M10-A 错误码 |
| `app/fastapi_app.py` | ~640 | 96% | +canon_router 注册 |

**审查结论：**
- ✅ `canon_routes.py`：7 个端点实现完整，Pydantic 请求/响应模型结构稳定，含 `request_id` + `timestamp` 统一响应结构。
- ✅ 幂等性：POST /canon/proposals 对重复 `proposal_id` 返回 409 + WCANON_PROPOSAL_DUPLICATE。
- ✅ 审计串联：所有写操作 `proposer_node_id` 含 `request_id`（格式：`api.canon_proposals::{request_id}`）。
- ✅ 错误码映射完整：400/404/409/422/500 均有对应 `reason_code`。
- ✅ `reject_proposal()`：status 改为 rejected，不修改 SSOT 文件，PermissionError 若非 pending。
- ✅ `list_proposals()`：分页查询，支持 status/canon_kind/target_doc_id 过滤，limit 硬限制 100。
- ⚠️ **发现**：`_get_canon_store()` 使用全局单例，多线程并发初始化时存在竞态条件（R118-1）。
- ⚠️ **发现**：`_get_ledger_from_app()` 延迟导入存在循环引用风险（R118-2，与 web_ui.py 模式一致，当前可接受）。
- ⚠️ **发现**：`canon_proposal_log.proposal_id` 无 UNIQUE 约束，高并发时幂等检查可能双写（R118-3）。

---

## 🧪 四、测试体系评估（M10-A 更新）

### 4.1 测试全量运行结果

```
1041 passed, 0 failed in 15.76s ✅
```

| 测试分类 | 文件数 | 通过 | 失败 |
|----------|--------|------|------|
| **契约测试** | ~100 | ~990 | 0 |
| **集成测试** | ~10 | ~30 | 0 |
| **E2E 测试** | ~10 | ~21 | 0 |
| **合计** | 120 | **1041** | **0** |

> **对比第三版**：7 项测试失败（F-21~F-24）已在 M8~M10-A 开发过程中全部修复。

### 4.2 M9/M10-A 新增测试覆盖

| 测试编号 | 测试文件 | 断言点 | 覆盖域 |
|---------|---------|--------|-------|
| T-WCanon-04~07 | `test_world_canon_*_contract.py` | 40+ | World Canon SSOT/同步/分槽/Evolution |
| T-Diary-03~04 | `test_diary_*_contract.py` | 15+ | 日记 drift_suggestion 关联 |
| T-Canon-API-01~08 | `test_canon_api_contract.py` | 10 | Canon 治理 REST API |

---

## 🔒 五、安全审计（M10-A 更新）

### 5.1 不变量合规扫描

| 不变量 | 检查项 | 结果 |
|--------|--------|------|
| **I1 单向链** | 搜索 `from host_brain.skills.impl` 旁路导入 | ✅ 仅 `impl/__init__.py` 内部注册，无旁路 |
| **I6 Token 必检** | 搜索 Skill 执行路径 bypass | ✅ 所有执行经 `loader.py` Token 校验 |
| **I8 审计强制** | 搜索执行无审计写入路径 | ✅ 所有 `_log_to_table` 通路可达 |
| **I9 拒绝必带码** | 搜索 REJECT 无 reason_code | ✅ `validate_governor_output` 拦截 |
| **I11 不伪执行** | 搜索 `print("已执行")` 等 | ✅ QA 拦截器 + 正则匹配覆盖 |
| **Canon 主权** | 搜索 SSOT 旁路写入 | ✅ 所有写入经 `upsert_document()` + `_write_lock` |

### 5.2 M10-A 新增安全检查

| 检查项 | 结果 |
|--------|------|
| Canon API 写操作审计串联 | ✅ 所有写操作 proposer_node_id 含 request_id |
| Canon API 幂等性 | ✅ 重复 proposal_id 返回 409，不重复写入 |
| Canon API 完成性表述拦截 | ✅ submit_proposal 调用 `_contains_execution_claim()` 拦截 |
| SSOT 文件写入串行化 | ✅ `_write_lock` 保证单进程串行写入 |
| proposal_id UNIQUE 约束 | ⚠️ 数据库层无 UNIQUE 约束（R118-3），依赖应用层幂等检查 |

---

## ⚠️ 六、发现项汇总（M10-A 更新）

### 🔴 P0 — 阻断性问题（0 项）

**无阻断性问题。**

### 🟡 P1 — 需跟进（16 项）

| # | 模块 | 发现 | 建议 |
|---|------|------|------|
| F-01 | `airlock/sandbox.py` | subprocess 后端无容器隔离 | 生产环境使用 Docker 后端 |
| F-02 | `airlock/sandbox.py` | Windows/Linux 超时行为差异 | Linux 部署环境回归验证 |
| F-04 | `governor/rate_limit_store.py` | Redis 后端未在真实环境验证 | 有 Redis 实例时集成测试 |
| F-05 | `audit/sqlite_ledger.py` | WAL 模式在 NFS 上不支持 | 部署文档标注存储介质要求 |
| F-06 | `audit/sqlite_ledger.py` | `_table_has_column` 无缓存 | 增加表结构缓存 |
| F-07 | Edge ASR | CPU 降级延迟可能不可接受 | 目标 Edge 设备实测 |
| F-08 | Edge TTS+ASR | GPU VRAM 叠加 OOM | Edge 设备压力测试 |
| F-11 | E2E 测试 | E2E 模拟 Edge 事件而非真实 RPC | 真实 Edge 环境集成验证 |
| F-12 | `requirements.txt` | 缺少 `redis`（可选依赖）说明 | 补充可选依赖文档 |
| **F-28** | `app/canon_routes.py` | `_get_canon_store()` 全局单例竞态（R118-1） | 多进程部署时改为依赖注入 |
| **F-29** | `app/canon_routes.py` | `_get_ledger_from_app()` 延迟导入循环引用风险（R118-2） | 后续重构为参数传递 |
| **F-30** | `audit/migrations/0016_canon_proposal_log.sql` | `proposal_id` 无 UNIQUE 约束（R118-3） | 添加 UNIQUE 约束 migration |
| **F-31** | `memory/canon_world_store.py` | Evolution approve 无回滚机制（R114-1） | 实现 `rollback_proposal()` |
| **F-32** | `scripts/runtime/start_host_side.ps1` | canon_sync DEGRADED 非阻断可能掩盖同步失败（R112-1） | 监控告警覆盖 STARTUP_CANON_SYNC_DEGRADED |
| **F-33** | `core/qa.py` | context_pack 分槽注入失败降级为空槽（R113-1） | 监控告警覆盖 WCANON_SLOT_VIOLATION |
| **F-34** | `memory/canon_world_store.py` | SSOT 文件并发写入无文件锁（R111-1） | 多进程部署时引入文件锁 |

### 🟢 P2 — 已知边界/建议改进（15 项）

| # | 模块 | 发现 | 建议 |
|---|------|------|------|
| F-13 | `app/main.py:908` | 裸 `print()` 调用 | 替换为 `logger.info()` |
| F-14 | `graph_executor.py:43` | 缺少返回类型标注 | 补充 `-> None` |
| F-15 | `fastapi_app.py:582,605` | 2 个 handler 缺返回类型标注 | 补充类型标注 |
| F-16 | `audit/sqlite_ledger.py` | 表名 f-string 插值（8 处） | 增加表名白名单校验 |
| F-18 | SQLite 单文件 | 高并发读写可能瓶颈 | 中期考虑 PostgreSQL 或只读副本 |
| F-19 | WAL checkpoint | 长期运行 WAL 文件增长 | 监控 WAL 大小 + 定时 checkpoint |
| F-20 | `sandbox.py` | 仅支持 Python 语言 | 按需扩展 JS/Shell |
| F-25 | `vts_driver.py:685` | `asyncio.get_event_loop()` DeprecationWarning | 改为 `asyncio.get_running_loop()` |
| F-26 | Docker 沙箱 | Docker 镜像构建依赖网络 | 支持预构建镜像本地加载 |
| F-27 | Docker 沙箱 | nsjail 备选在非 Linux 不可用 | 部署文档标注平台要求 |
| **F-35** | `app/canon_routes.py` | approve 端点两次 DB 操作非事务（R119-1） | 多进程部署时使用事务包裹 |
| **F-36** | `app/canon_routes.py` | reject 审计写入失败只记录 warning（R119-2） | 后续纳入事务确保原子性 |
| **F-37** | `release/manifests/m10a_release.json` | 未锁定 canon_world_store.py 和 sqlite_ledger.py hash（R120-1） | 扩展 manifest 锁定更多核心文件 |
| **F-38** | `memory/canon_world_store.py` | `list_proposals()` 分页使用 LIMIT/OFFSET，深页码性能退化（R112-2） | 后续改为 cursor-based 分页 |
| **F-39** | `memory/episodic.py` | 日记 source_intent_id 跨表关联可能断链（R115-1） | 增加 intent_id 完整性校验 |

---

## 📊 七、综合评分（M10-A 更新）

| 维度 | 评分 | 较上次 | 说明 |
|------|------|--------|------|
| **架构设计** | ⭐⭐⭐⭐⭐ | — | Host-Edge 分体 + 单向链 + 票据化执行 + Canon 治理 API |
| **代码质量** | ⭐⭐⭐⭐⭐ | — | 0 裸 except / 0 TODO / 0 硬编码密钥 / frozen dataclass |
| **安全性** | ⭐⭐⭐⭐⭐ | — | Docker 容器隔离 + Canon 主权保护 + 审计串联 |
| **可审计性** | ⭐⭐⭐⭐⭐ | ⬆️ | 26 张审计表 + 148 个错误码 + Canon 治理 API 审计 |
| **可测试性** | ⭐⭐⭐⭐⭐ | ⬆️ | **1041 通过 / 0 失败**（第三版 7 项失败全部修复） |
| **功能完成度** | ⭐⭐⭐⭐⭐ | ⬆️ | TASK-0000~0120 / M1~M10-A 全部完成 |
| **可运行性** | ⭐⭐⭐⭐⭐ | — | FastAPI + Canon REST API + Docker sandbox + Edge 重连/恢复 |
| **可观测性** | ⭐⭐⭐⭐⭐ | — | 面板 + 跨端时间线 + Canon 治理 API 可观测 |

### 总体评分：**⭐⭐⭐⭐⭐ / 5（工程成熟度极高，1041 tests 全部通过）**

---

## 📋 八、与第三版对比（改进总结）

| 上次发现 | 优先级 | 状态 | 解决方式 |
|---------|--------|------|---------|
| F-21 VTS DEGRADED 断言失败（2 项） | P1 | ✅ **已解决** | M8/M9 开发过程中修复 |
| F-22 sandbox docker mock 路径错误（2 项） | P1 | ✅ **已解决** | M8/M9 开发过程中修复 |
| F-23 VTS 重连/幂等断言失败（2 项） | P1 | ✅ **已解决** | M8/M9 开发过程中修复 |
| F-24 release contract 异常处理不匹配（1 项） | P1 | ✅ **已解决** | M8/M9 开发过程中修复 |

M8~M10-A 新增能力：

| 新增能力 | 里程碑 | 说明 |
|---------|--------|------|
| 运行时集成闭环（ServiceRegistry + MetricsCollector） | M8 | TASK-0107~0110 |
| World Canon SSOT + 索引同步 + 注入分槽 | M9 | TASK-0111~0113 |
| Evolution 提案审核流程 | M9 | TASK-0114 |
| 日记 drift_suggestion 关联 | M9 | TASK-0115 |
| Canon 治理 REST API（7 端点） | M10-A | TASK-0117~0119 |
| reject_proposal() + list_proposals() | M10-A | TASK-0117 |
| M10-A 发布清单（m10a_release.json） | M10-A | TASK-0120 |

---

## 🏆 九、结论

**Nastume Ai 在 TASK-0000~0120 / M1~M10-A 全部完成后，已达到"Canon 治理闭环"的工程成熟度。**

- ✅ **57,527 行代码**（33,352 业务 + 24,175 测试）全部有机组织
- ✅ **1041 项测试全部通过**（0 失败），第三版 7 项失败全部修复
- ✅ **26 张审计表 + 148 个错误码常量 + 19 个 SQL 迁移脚本** 覆盖全链路
- ✅ **Canon 治理 REST API**（7 端点）完整实现，审计串联 + 幂等性 + 结构稳定
- ✅ 核心不变量（单向链/Token 必检/审计强制/拒绝必带码/不伪执行/Canon 主权）全部通过
- ✅ Docker 容器级沙箱隔离已就位
- ✅ Edge 设备恢复韧性：VTS 自动重连 + ASR 设备恢复 + 事件补发
- ⚠️ 剩余 16 个 P1 发现项（含 7 项 M10-A 新增）
- ⚠️ 剩余 15 个 P2 建议项（含 5 项 M10-A 新增）

**风险详情已记录在** [risk_registry.md](../60_trace/risk_registry.md) **中（v3.0，覆盖 TASK-0065~0120）。**

---

> 本报告基于全量源码逐模块审阅 + 1041 项测试全量运行 + 不变量静态扫描 + 安全审计生成。
