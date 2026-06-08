受众：MASTER
标题：记忆系统完整性实测报告（2026-05-31）

# 记忆系统完整性实测报告（2026-05-31）

版本：v1.0
维护日期：2026-05-31
标签：memory、life-memory、recall-fusion、runtime-e2e、host

## 结论

当前 Host 侧记忆系统完整性通过。

本次实测覆盖了五层：

- 记忆写入与 LifeMemoryStore 单元行为。
- MemoryHub 生产环境依赖接线。
- SQLite 事实账本、Chroma 向量集合、Neo4j 图谱、Redis 基础服务。
- Life memory 入口覆盖、生命周期、召回场景与验收套件。
- 线上 `/v1/chat` surface memory recall E2E。

## 当前运行面

- Host API：`http://127.0.0.1:18080`
- Obs Backend：`http://127.0.0.1:18082`
- Monitor Proxy：`http://127.0.0.1:18180`
- Chroma：`http://127.0.0.1:18000`
- Redis：`127.0.0.1:6379`
- Neo4j Bolt：`127.0.0.1:17687`
- Edge SSH：`edge-lan`
- Edge HTTP：使用局域网 IP，例如 `192.168.1.101`，不要把 `edge-lan` 当普通 HTTP DNS 名使用。

## 实测命令

### 1. 记忆相关单测矩阵

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_life_memory_store.py `
  tests/unit/test_life_memory_collection_recall.py `
  tests/unit/test_life_memory_recall_route.py `
  tests/unit/test_life_memory_acceptance_suite.py `
  tests/unit/test_life_memory_entrypoint_coverage.py `
  tests/unit/test_life_memory_lifecycle_scenario_batch.py `
  tests/unit/test_life_memory_recall_batch_scenarios.py `
  tests/unit/test_background_event_memory.py `
  tests/unit/test_surface_memory_recall.py `
  tests/unit/test_recall_fusion_semantic_memory.py `
  tests/unit/test_recall_fusion_persona_memory_ranking.py `
  tests/unit/test_memory_distillation.py `
  tests/unit/test_memory_guard.py `
  tests/unit/test_memory_steward_skills.py `
  tests/unit/test_observability_persist_chat_memory.py `
  tests/unit/test_person_memory_identity.py `
  tests/unit/test_episodic_memory_identity.py `
  tests/unit/test_chat_retrieval_helpers_surface_memory.py `
  tests/unit/test_surface_memory_ab_tool.py `
  tests/unit/test_validate_surface_memory_emotion_loop.py
```

结果：`278 passed, 1 warning`。

### 2. MemoryHub 生产环境集成测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_memory_hub_prod_env.py
```

结果：`5 passed, 1 warning`。

### 3. Life memory 运行时验收套件

```powershell
.\.venv\Scripts\python.exe scripts\runtime\run_life_memory_acceptance_suite.py `
  --base-url http://127.0.0.1:18080 `
  --sample-limit 20 `
  --json
```

结果：`success=true`，`5/5` checks passed。

关键指标：

- deterministic recall scenarios：`18/18 passed`
- production ledger coverage：passed
- sleep replay dry-run：passed
- `issue_count=0`
- `critical_issue_count=0`
- `collection_issue_count=0`
- `semantic_provenance_unresolved_count=0`

### 4. Life memory 场景批量脚本

```powershell
.\.venv\Scripts\python.exe scripts\runtime\run_life_memory_entrypoint_scenario_batch.py
.\.venv\Scripts\python.exe scripts\runtime\run_life_memory_lifecycle_scenario_batch.py
.\.venv\Scripts\python.exe scripts\runtime\run_life_memory_recall_scenario_batch.py
```

结果：

- entrypoint scenario batch：`PASS`
- lifecycle scenario batch：`8/8 passed`
- recall scenario batch：`18/18 passed`

### 5. 线上 surface memory recall E2E

默认轻小说深评召回场景：

```powershell
.\.venv\Scripts\python.exe scripts\runtime\test_surface_memory_recall_e2e.py `
  --base-url http://127.0.0.1:18080 `
  --timeout-sec 60 `
  --trace-timeout-sec 60
```

结果：`passed=true`。

偏好记忆召回场景：

```powershell
.\.venv\Scripts\python.exe scripts\runtime\test_surface_memory_recall_e2e.py `
  --base-url http://127.0.0.1:18080 `
  --timeout-sec 60 `
  --trace-timeout-sec 60 `
  --scenario preference_recall `
  --message "还记得我说推荐格式要怎样吗？"
```

说明：`preference_recall` 会种入一条可复现的推荐格式偏好记忆，并验证线上回复与 recall packet 是否命中该偏好事实。

## 存储层快照

SQLite 审计账本：`host_brain/data/audit.sqlite3`

本次抽样确认的核心表：

- `life_episode`
- `life_evidence`
- `life_memory_collection`
- `life_memory_collection_item`
- `life_memory_compaction`
- `life_memory_correction`
- `life_memory_entity`
- `life_memory_entity_mention`
- `semantic_memory`
- `world_canon_doc`
- `canon_proposal_log`
- `canon_change_log`
- `memory_ops_log`

本次实测时的代表性计数：

- `life_episode`：约 `27049`
- `life_evidence`：约 `28342`
- `life_memory_collection`：`1008`
- `life_memory_collection_item`：`4215`
- `semantic_memory`：`72`
- `memory_ops_log`：`10995`

Chroma：

- `episodic`：`2264`
- `surface_original_quotes`：`0`

Neo4j：

- Bolt 查询成功。
- `label_count=5`

## 判定标准

记忆系统完整性通过需要同时满足：

- Host `/health?skip_dependency_probes=1` 返回 200。
- Host `/ready` 返回 READY。
- Chroma heartbeat 返回 200。
- Redis 与 Neo4j 端口可连通。
- SQLite 记忆事实表存在且非空。
- 单测矩阵与 MemoryHub 集成测试通过。
- Life memory acceptance suite 无 critical issue。
- entrypoint、lifecycle、recall 三类场景批量脚本通过。
- `/v1/chat` surface memory recall E2E 至少一个领域场景通过。

## 已知非阻塞项

- `surface_original_quotes=0` 不是本次完整性失败条件；当前主链可通过 `life_episode`、collection、semantic memory、deep review packet 完成召回。
- 运行时验收和 E2E 会写入 deterministic probe episode、evidence 与日志；不要把这些运行态写入提交到仓库。
- Python 输出中的 LangChain pending deprecation warning 不影响当前记忆链路。
- 旧版 `test_surface_memory_recall_e2e.py` 对任意 `--message` 都套用轻小说深评断言，曾导致“推荐格式偏好”召回被误判失败；现已拆出 `--scenario preference_recall` 与 `--scenario generic_memory_recall`。

## 后续维护建议

- 新增记忆入口或 recall policy 时，同步扩展 `run_life_memory_entrypoint_scenario_batch.py`。
- 新增领域化召回形态时，优先在 `test_surface_memory_recall_e2e.py --scenario` 增加断言分支，不要复用轻小说深评断言。
- 若线上召回失败，先看 `runtime/host/logs/surface_memory_recall_e2e_*.json`，再查 `graph_run_trace` 与 `retrieval_event_log`。
