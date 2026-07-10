受众：PROJECT
标题：TASK-0464 Pentest 回填与 Domain-only 切换验证报告

# TASK-0464 / LTE-WP-11 验证报告

日期：2026-07-10
状态：VERIFIED
测试合同：T-NLE-02
基线提交：`093c1f58d`（TASK-0463）

## 1. 结论

Pentest 历史知识已通过可重复的 `backfill -> shadow -> bounded stable final_delta` 流程迁入领域 Store。迁移器闭集扫描受保护原工作区 `canon/world` 的全部 87 份 JSON，按文档 id/tag 规则纳入 83 份 Pentest eligible 文档，明确跳过 4 份非 Pentest 文档；目标为仓库外临时 SQLite DB。源/目标计数、包含检索 metadata 的 source hash、evidence refs 投影、全量 digest 和 5 类检索样本全部一致，并在目标 DB 内写入 `VERIFIED` cutover receipt。

active Pentest 代码路径已切为领域读写。`host_brain/pentest` 内直接 WorldCanon import、`upsert_document()` 调用和 active `CanonVectorQuery()` 构造均为 0。成功攻击链只写领域 Store，并生成 `PENDING_REVIEW` promotion candidate；不会直接更新 Canon。若 legacy 目录存在 eligible 文档而实际目标 DB 没有匹配 receipt，或 source/target digest 漂移，`domain` access 以稳定 reason code fail closed，不会静默打开空库。

本报告的真实迁移证据只对应临时证据 DB；仓库配置的实际 `host_brain/data/audit.sqlite3` 未被本次演练选作 target。部署前必须先停止 legacy writer，并对实际目标运行同一迁移命令直至 receipt 为 `VERIFIED`，之后才能让 `domain` mode 正常启动。本线程 MASTER 已明确授权 Goal 自动执行全部重构、无需逐任务再次确认；未推送远端。

## 2. 真实只读迁移演练

| 项目 | 结果 |
|---|---:|
| source ref | `canon-readonly://protected-original/world` |
| target ref | `temp-db://task0464-final-evidence` |
| scanned / eligible / skipped | 87 / 83 / 4 |
| skipped reason | 4 × `SOURCE_NOT_PENTEST_ELIGIBLE` |
| 全部扫描文件数（迁移前/后） | 87 / 87 |
| 全部扫描文件 SHA-256 变化 | 0 |
| source / target count | 83 / 83 |
| source digest | `7608e9bb38301364bdcc25920500105abe37780e6e0d8ff2253d1a55f7a5e0d2` |
| target digest | `7608e9bb38301364bdcc25920500105abe37780e6e0d8ff2253d1a55f7a5e0d2` |
| backfill / shadow / final_delta | 全部 `matches=true` |
| final-delta stability | pass 1 / max 3；`stable=true` |
| legacy writer quiesce contract | `LEGACY_WRITERS_QUIESCED_UNTIL_VERIFIED_RECEIPT` |
| cutover receipt | `task-0464-7608e9bb38301364` / `VERIFIED` |
| 检索样本 | command、pitfall、reflection、successful_chain、technique；5/5 一致 |
| WorldCanon write count | 0 |
| domain cutover ready | true |
| manifest schema | Draft 2020-12 校验通过 |

完整的不含正文证据见 `NLE_WP11_TASK0464_MIGRATION_MANIFEST_20260710.json`。manifest 仅保存 store refs、eligible/skip reason、entry/file hashes、计数、digest、entry ids、稳定轮次和检索查询条件；未复制 Canon 正文。

证据演练使用的关键 CLI 合同如下；`<temporary-evidence-db>` 位于仓库外：

```powershell
python scripts/pentest/migrate_pentest_canon_knowledge.py --source-root D:\Natsume_Ai\host_brain\canon\world --target-db <temporary-evidence-db> --manifest docs/35_quality/audits/NLE_WP11_TASK0464_MIGRATION_MANIFEST_20260710.json --source-ref canon-readonly://protected-original/world --target-ref temp-db://task0464-final-evidence --migration-id task-0464-7608e9bb38301364 --legacy-writers-quiesced --max-final-delta-passes 3
```

## 3. 切换范围

下列 active reader/writer 已逐一切换到 `PentestKnowledgeAccess` / `PentestKnowledgeStore`：

- `action_policy.py`
- `autonomous_kernel.py`
- `campaign_memory.py`
- `experience_retriever.py`
- `knowledge_ingester.py`
- `writeup_ingester.py`
- `agent/knowledge.py`
- `agent/evolution_core.py`
- `agent/llm_mcts_advisor.py`

`campaign_memory` 原先引用不存在的 `get_world_canon_store()` / `scan_documents()` 签名，现已改为可测试的领域 upsert/retrieve。新领域写入使用 `pentest-domain-*` namespace，避免与 legacy id 跨 lineage 覆盖；hydrate 同时兼容旧/新前缀。kernel 与 EvolutionCore 在 run 边界关闭自有 KnowledgeBase/SQLite handle；MCTS 与 ingester 使用 context-managed access。

## 4. 模式、promotion 与回滚

- 默认模式：`HOST_PENTEST_KNOWLEDGE_MODE=domain`，只读写领域 Store；默认 DB 路径按模块绝对锚定，不受 CWD 影响。
- receipt gate：legacy eligible 文档非空时，必须有与当前 source count/digest、legacy-derived target count/digest 一致的 `VERIFIED` receipt；缺 receipt、source drift、target drift、跨 lineage collision 均 fail closed。
- migration gate：CLI 必须显式传 `--legacy-writers-quiesced`；final delta 使用有限稳定循环，源在写后变化会重试，持续变化超限则 `FAILED` 且不写 receipt。
- promotion：领域 entry 先持久化，再生成按 entry/source hash/reason 幂等的 `PENDING_REVIEW` candidate；不调用 Canon API。
- shadow 回滚：设置 `HOST_PENTEST_KNOWLEDGE_MODE=shadow` 后，从只读旧目录返回 legacy 结果并执行 domain 对账；所有新写仍进入领域 Store。
- shadow 默认目录基于模块绝对路径解析，与进程 CWD 无关；可由 `HOST_PENTEST_LEGACY_CANON_ROOT` 显式覆盖。
- rollback 不删除领域表、promotion candidate 或旧 Canon 文件，也不恢复直接 Canon 写入。

部署到实际目标时必须把 `--target-db` 换成该部署实例的真实 DB，并把部署 manifest 写到新的部署证据路径；不得把本报告的临时 DB receipt 当作实际目标 receipt。运行期间 legacy writer 必须保持停止，命令返回 `VERIFIED` 后再启动 domain reader/writer。

## 5. 数据最小化

ingester/writeup 的 durable `source_ref` 只保存内容 SHA-256；外部 doc label、basename、路径、run id 和 engagement id 均不原样持久化，而是使用固定 scheme + digest 的 opaque ref。`PentestKnowledgeEntry` 统一边界会拒绝 Bearer token、`sk_*` key、API key/cookie/private key 等 secret-like 内容，并只接受已验证 scheme 的 evidence refs；entry id、title、payload、tags 和 source ref 均进入同一反查范围。

## 6. 静态边界

TASK-0461 精确 allowlist 中 9 条 Pentest WorldCanon import 和 8 条 Pentest direct-upsert 债务已全部删除，stale allowlist 为 0。`NLE-BND-PENTEST-CANON-VECTOR-READER` 同时识别直接构造、import alias 和 module attribute 构造；三类 fixture 均会失败。剩余 4 条既有边界债务均属于 AgentRuntime/Edge，与本任务无关。

## 7. 验证结果

| 验证集 | 结果 |
|---|---:|
| TASK-0464/TASK-0463 + offline Pentest 调用链 | 124 passed；4 warnings；24.33s |
| NLE static scan | passed；0 violations |
| Python compileall | passed |
| 完整 Pentest/OOB 网络套件 | 未作为 Seal；会连接本机代理，见下述边界 |
| git diff check | passed |

已知的 `phase3_access_control.py` invalid-escape DeprecationWarning 属于 TASK-0461 已登记的既有 warning debt，本任务未扩域修改。

Seal 使用以下可复现命令自然结束；`HOST_PENTEST_KNOWLEDGE_DB_PATH` 指向仓库外的一次性临时 SQLite 文件：

```powershell
$env:HOST_PENTEST_KNOWLEDGE_DB_PATH='<temporary-sqlite-path>'
$env:HOST_PENTEST_LEGACY_CANON_ROOT='<empty-temporary-directory>'
$env:HOST_PENTEST_KNOWLEDGE_MODE='domain'
$env:PENTEST_ONLINE_RESEARCH_DISABLED='1'
$env:PENTEST_LLM_DISABLED='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
pytest -q tests/contract/test_pentest_knowledge_store_contract.py tests/contract/test_pentest_knowledge_domain_cutover.py tests/unit/test_pentest_knowledge_online_learn.py tests/unit/test_nle_boundary_scan.py tests/contract/test_nle_boundary_scan_contract.py tests/unit/test_pentest_domain_runtime_regressions.py tests/unit/test_pentest_online_research.py tests/unit/test_pentest_failure_classifier_offline.py tests/unit/test_pentest_agent_loop_contracts.py tests/unit/test_pentest_cognitive_kernel.py
```

124 项按测试文件精确分解如下：

| 测试文件 | collected / passed |
|---|---:|
| `test_pentest_knowledge_store_contract.py` | 26 |
| `test_pentest_knowledge_domain_cutover.py` | 23 |
| `test_pentest_knowledge_online_learn.py` | 24 |
| `test_nle_boundary_scan.py` | 3 |
| `test_nle_boundary_scan_contract.py` | 2 |
| `test_pentest_domain_runtime_regressions.py` | 9 |
| `test_pentest_agent_loop_contracts.py` | 12 |
| `test_pentest_cognitive_kernel.py` | 7 |
| `test_pentest_online_research.py` | 14 |
| `test_pentest_failure_classifier_offline.py` | 4 |

该集合覆盖 receipt/stability/collision/closed-set migration、domain store/manifest/promotion、campaign/experience/ingester、runtime id 最小化、static scan、AgentLoop、CognitiveKernel 生命周期、使用显式 mock client 的 OnlineResearch，以及 failure classifier 的 rules-only/kill-switch 契约。`PENTEST_ONLINE_RESEARCH_DISABLED=1` 禁止隐式 research HTTP client；`PENTEST_LLM_DISABLED=1` 禁止 failure classifier 与 CognitiveEngine 的 LLM slow-path；pytest 默认也禁止这些 Pentest LLM 调用，除非单测显式设置 `PENTEST_TEST_ALLOW_LLM=1`。完整 OOB/真实网络套件不属于此 Seal。

完整 `test_pentest*.py` 聚合运行不作为本任务绿色证据：未隔离的 autonomous/OOB 测试会经 `127.0.0.1:7890` 本机代理尝试真实网络，运行已被主动终止。单独诊断 `test_pentest_autonomous_redteam.py` 得到 83 passed / 26 failed；其中代表性 route artifact 失败及 `autonomous_evidence` 的 3 个 candidate/rejected/blocked-path 失败已在未修改的 TASK-0463 基线 `093c1f58d` 复现，属于既有算法/测试 debt。本任务按 Out of Scope 未修改证据判定、Pentest 策略或 OOB 算法，也未把这些环境型测试冒充 Seal。

## 8. 停止条件复核

- 内容/source hash 对账失败：否。
- evidence refs 投影对账失败：否。
- shadow 关键检索样本漂移：否。
- 源文件被修改或删除：否。
- 实际目标 DB 缺 receipt 时是否可能静默 domain 启动：否，fail closed。
- 迁移期间 source 持续变化是否可能写 VERIFIED receipt：否。
- legacy/domain 同 id 是否可能互相覆盖：否。
- domain mode WorldCanon 读写或 Canon vector active reader：0。
- 持续双写：未引入。
- Canon 文件删除：0。

TASK-0464 的停止条件未触发。
