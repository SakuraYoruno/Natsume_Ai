content = """

---

## M9 World Canon 闭环 + 日记 Evolution 关联（TASK-0111~0115）

> 对应 DevSpec §19；PRD M9（FR-WCANON-001~003、FR-DIARY-002）。
> 所有任务当前状态：TASK_READY（2026-03-03）。

### TASK-0111 World Canon 条目资产：文件 SSOT + 最小种子条目（M9 P0）
- 任务描述：在 `host_brain/canon/world/` 下建立 World Canon 文件 SSOT，写入最小种子条目（系统术语/架构设定/关键不变量各 1 条），并确保条目结构符合 DevSpec §19.1.1 的字段约束。
- 输入：DevSpec §19.1.1；host_brain/canon/world/（现有目录，可能为空）。
- 输出：
  - `host_brain/canon/world/wc-001-system-terms.json`（新增）：系统术语种子条目；
  - `host_brain/canon/world/wc-002-architecture-invariants.json`（新增）：架构不变量种子条目；
  - `host_brain/canon/world/wc-003-persona-core.json`（新增）：人格核心设定种子条目；
  - 每个条目包含：doc_id/version/title/tags/body/evidence_level=CANON_REFERENCE_ONLY/status=active/created_at/updated_at；
  - `tests/fixtures/canon/world/`（新增）：种子条目的 fixture 副本，用于 contract 测试。
- 验收标准：
  - 所有条目 evidence_level=CANON_REFERENCE_ONLY；
  - body 不含完成性表述（QA 校验通过）；
  - 条目文件可被 canon_sync.py 扫描并写入 world_canon_doc 索引；
  - T-WCanon-04 前置条件满足（有可同步的条目）。
- 涉及文件路径：host_brain/canon/world/wc-001-system-terms.json（新增）；host_brain/canon/world/wc-002-architecture-invariants.json（新增）；host_brain/canon/world/wc-003-persona-core.json（新增）；tests/fixtures/canon/world/。
- 关联需求/测试：FR-WCANON-001；T-WCanon-04（前置）。

### TASK-0112 World Canon 索引同步：world_canon_doc 表 + canon_sync 幂等扫描（M9 P0）
- 任务描述：在 SQLite Ledger 中新增 `world_canon_doc` 表与 `canon_proposal_log` 表，并扩展 `canon_sync.py` 实现幂等扫描逻辑（相同 hash 不重复写 change_log，失败记录 CANON_SYNC_FAILED）。
- 输入：DevSpec §19.1.2/19.1.4；host_brain/audit/sqlite_ledger.py（现有）；host_brain/memory/canon_sync.py（现有）；TASK-0111 输出的种子条目。
- 输出：
  - `host_brain/audit/migrations/0009_world_canon_doc.sql`（新增）：world_canon_doc 表迁移；
  - `host_brain/audit/migrations/0010_canon_proposal_log.sql`（新增）：canon_proposal_log 表迁移；
  - `host_brain/memory/canon_sync.py` 扩展：`sync_world_canon()` 方法（扫描→hash→对比→写 change_log，幂等）；
  - `host_brain/core/errors.py` 扩展：`CANON_SYNC_FAILED`、`WCANON_SLOT_VIOLATION`、`WCANON_PROPOSAL_INVALID` 错误码；
  - `tests/contract/test_world_canon_sync_contract.py`（新增）：T-WCanon-04 验收测试（幂等性 + 失败可统计）；
  - `tests/fixtures/canon/world/` 扩展：sync_result_expected.json。
- 验收标准：
  - 相同条目重复同步不产生重复 canon_change_log 条目（幂等）；
  - 同步失败时 CANON_SYNC_FAILED 可统计；
  - world_canon_doc 表字段与 DevSpec §19.1.2 一致；
  - T-WCanon-04 全部通过。
- 涉及文件路径：host_brain/audit/migrations/0009_world_canon_doc.sql（新增）；host_brain/audit/migrations/0010_canon_proposal_log.sql（新增）；host_brain/memory/canon_sync.py；host_brain/core/errors.py；tests/contract/test_world_canon_sync_contract.py（新增）；tests/fixtures/canon/world/。
- 关联需求/测试：FR-WCANON-002；T-WCanon-04。

### TASK-0113 World Canon 注入分槽：context_pack 独立槽位 + QA 拦截 Canon-as-fact（M9 P0）
- 任务描述：扩展 `context_pack.py` 实现 World Canon 独立槽位注入（Top-K，evidence_level=CANON_REFERENCE_ONLY），并扩展 `qa.py` 拦截"World Canon 内容 + 完成性表述"组合，记录 CANON_AS_FACT_VIOLATION。
- 输入：DevSpec §19.1.3；host_brain/core/context_pack.py（现有）；host_brain/core/qa.py（现有）；TASK-0112 输出的 world_canon_doc 索引。
- 输出：
  - `host_brain/core/context_pack.py` 扩展：`build_world_canon_slot()` 方法，从 world_canon_doc 查询 Top-K active 条目，注入 authoritative.world_canon 槽位；
  - `host_brain/core/qa.py` 扩展：`check_canon_as_fact()` 方法，检测叙事中"Canon 内容 + 完成性表述"组合；
  - `tests/contract/test_world_canon_slot_contract.py`（新增）：T-WCanon-05（分槽隔离）+ T-WCanon-06（QA 拦截）验收测试；
  - `tests/fixtures/canon/world/` 扩展：context_pack_with_world_canon.json / qa_canon_as_fact_bad.json。
- 验收标准：
  - World Canon 条目只出现在 authoritative.world_canon 槽位，不混入其他槽位；
  - 叙事含"Canon 内容 + 完成性表述"时 QA 拦截并记录 CANON_AS_FACT_VIOLATION；
  - 重写后仍失败则降级为 Host 最小模板；
  - T-WCanon-05/T-WCanon-06 全部通过。
- 涉及文件路径：host_brain/core/context_pack.py；host_brain/core/qa.py；tests/contract/test_world_canon_slot_contract.py（新增）；tests/fixtures/canon/world/。
- 关联需求/测试：FR-WCANON-001；FR-WCANON-003；FR-CANON-001；T-WCanon-05；T-WCanon-06。

### TASK-0114 World Canon Evolution 提案：canon_proposal_log 写入 + 审核流程（M9 P1）
- 任务描述：实现 World Canon Evolution 提案的写入与审核流程：提案写入 `canon_proposal_log`（status=pending），审核通过后触发 `canon_sync.py` 更新文件 SSOT，所有状态变更写 `canon_change_log`（含变更前后 hash）。
- 输入：DevSpec §19.1.4；TASK-0112 输出的 canon_proposal_log 表；host_brain/memory/canon_world_store.py（现有）；host_brain/memory/canon_sync.py（扩展后）。
- 输出：
  - `host_brain/memory/canon_world_store.py` 扩展：`submit_proposal()` 方法（写入 canon_proposal_log，status=pending）；`approve_proposal()` 方法（状态改为 approved，触发 sync，写 change_log）；
  - `tests/contract/test_world_canon_evolution_contract.py`（新增）：T-WCanon-07 验收测试（提案写入/审核/变更 hash 可回放）；
  - `tests/fixtures/canon/world/` 扩展：proposal_pending.json / proposal_approved.json / change_log_expected.json。
- 验收标准：
  - 提案写入后 status=pending，可在 canon_proposal_log 查询；
  - 审核通过后 status=approved，canon_change_log 含变更前后 hash；
  - 非白名单流程不得直接修改 status（contract 测试断言）；
  - T-WCanon-07 全部通过。
- 涉及文件路径：host_brain/memory/canon_world_store.py；host_brain/memory/canon_sync.py；tests/contract/test_world_canon_evolution_contract.py（新增）；tests/fixtures/canon/world/。
- 关联需求/测试：FR-WCANON-002；T-WCanon-07。

### TASK-0115 日记 Evolution 提案关联：drift_suggestion 提取 + QA 校验 + canon_proposal 写入（M9 P1）
- 任务描述：扩展 `diary_writer.py`，在日记生成后提取 LLM 输出中的可选 `drift_suggestion` 字段，经 QA 校验后写入 `canon_proposal_log`（proposer=diary_writer，source_intent_id=日记 intent_id）；校验失败则丢弃并记录 WCANON_PROPOSAL_INVALID，不阻塞日记写入。
- 输入：DevSpec §19.2；host_brain/skills/impl/diary_writer.py（现有）；host_brain/memory/canon_world_store.py（TASK-0114 扩展后）；host_brain/core/qa.py（TASK-0113 扩展后）。
- 输出：
  - `host_brain/skills/impl/diary_writer.py` 扩展：`_extract_drift_suggestion()` 方法（提取并校验 drift_suggestion 字段）；`_submit_evolution_proposal()` 方法（调用 canon_world_store.submit_proposal()）；
  - `host_brain/core/errors.py` 扩展：`DIARY_EVOLUTION_PROPOSAL_SKIPPED` 错误码；
  - `tests/contract/test_diary_evolution_contract.py`（新增）：T-Diary-03（提案写入 + source_intent_id 串联）+ T-Diary-04（QA 拦截 + 日记正常写入）验收测试；
  - `tests/fixtures/diary/` 扩展：diary_with_drift_suggestion.json / diary_drift_suggestion_bad.json。
- 验收标准：
  - 日记含合规 drift_suggestion 时，canon_proposal_log 写入成功，source_intent_id 可追溯到日记 intent_id；
  - drift_suggestion.suggested_body 含伪执行句时，提案被丢弃，日记正常写入，WCANON_PROPOSAL_INVALID 可统计；
  - 无 drift_suggestion 时日记正常写入，DIARY_EVOLUTION_PROPOSAL_SKIPPED 可观测；
  - T-Diary-03/T-Diary-04 全部通过。
- 涉及文件路径：host_brain/skills/impl/diary_writer.py；host_brain/core/errors.py；tests/contract/test_diary_evolution_contract.py（新增）；tests/fixtures/diary/。
- 关联需求/测试：FR-DIARY-002；T-Diary-03；T-Diary-04。
"""

with open('D:/nastume/Natsume_Ai/docs/30_devspec/DevTasks.md', 'a', encoding='utf-8') as f:
    f.write(content)
print('done')
