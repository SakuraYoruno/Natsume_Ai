content = """

---

## §19 M9 World Canon 闭环 + 日记 Evolution 关联

> 对应 PRD §4.12（FR-WCANON-001~003）、§4.23（FR-DIARY-002）。
> 目标：补全 PRD 中唯一"已定义但完全未实现"的功能域（World Canon 独立槽位注入闭环），并打通"日记→设定演化"提案链路。

---

### 19.1 World Canon 独立槽位注入闭环（FR-WCANON-001~003）

#### 19.1.1 World Canon 条目结构（文件 SSOT）

落点：`host_brain/canon/world/`（每个条目一个 JSON 文件）。

**条目最小字段**（语义固定）：
- doc_id：全局唯一 ID（如 wc-001）
- version：语义版本（如 1.0.0）
- title：条目标题
- tags：标签列表（如 architecture / invariant）
- body：条目正文（设定/术语/不变量描述）
- evidence_level：固定为 CANON_REFERENCE_ONLY
- status：active | deprecated | draft
- created_at / updated_at：ISO8601

**硬约束**：
- evidence_level 固定为 CANON_REFERENCE_ONLY，不得修改；
- body 不得包含"已执行/已保存/已确认"等完成性表述；
- 任何条目变更必须 bump version 并写 canon_change_log。

#### 19.1.2 World Canon 索引层（SQLite）

表：world_canon_doc（落点：host_brain/audit/sqlite_ledger.py 或独立迁移文件）。

最小字段：
- doc_id TEXT PRIMARY KEY
- version TEXT NOT NULL
- hash TEXT NOT NULL（sha256(body + version)）
- status TEXT NOT NULL（active | deprecated | draft）
- source_path TEXT NOT NULL（相对于 host_brain/canon/world/ 的路径）
- updated_ts INTEGER NOT NULL（Unix ms）

**同步契约**（canon_sync.py）：
- 扫描 host_brain/canon/world/ → 计算 hash → 与索引对比 → 差异写 canon_change_log（canon_kind=world）；
- 同步必须幂等（相同 hash 不重复写 change_log）；
- 同步失败不得导致系统崩溃，必须降级并记录 CANON_SYNC_FAILED。

#### 19.1.3 注入分槽隔离（context_pack.authoritative.world_canon）

**注入口径**（context_pack.py）：
- World Canon 条目只能进入 context_pack.authoritative.world_canon；
- 不得与 style_pack、retrieval.episodic、recent_events 混槽；
- 每个注入条目必须携带 evidence_level=CANON_REFERENCE_ONLY；
- 注入数量上限：Top-K（默认 5，可配置）。

**QA 联动**（qa.py）：
- 叙事输出中若出现"World Canon 内容 + 完成性表述"组合，必须拦截并记录 CANON_AS_FACT_VIOLATION；
- 拦截后允许重写 1 次；仍失败降级为 Host 最小模板。

#### 19.1.4 World Canon 更新流程（Evolution 提案）

**提案写入**（canon_proposal_log）最小字段：
- proposal_id TEXT PRIMARY KEY
- canon_kind TEXT NOT NULL（world | style）
- doc_id TEXT NOT NULL
- proposed_version TEXT NOT NULL
- proposed_body TEXT NOT NULL
- proposer TEXT NOT NULL（scheduler | user | diary_writer）
- source_intent_id TEXT（来源 intent_id，可空）
- status TEXT NOT NULL（pending | approved | rejected）
- created_at INTEGER NOT NULL
- reviewed_at INTEGER（可空）

**审核流程**：
- 提案写入后状态为 pending；
- 只有 Host 白名单流程（或 Master 显式确认）可将状态改为 approved；
- approved 后 canon_sync.py 将新版本写入文件 SSOT 并更新索引；
- 任何状态变更写 canon_change_log（含变更前后 hash）。

#### 19.1.5 回归测试编号（新增）

- T-WCanon-04（索引同步幂等性）：相同 hash 的条目重复同步不产生重复 canon_change_log 条目；同步失败时 CANON_SYNC_FAILED 可统计。
- T-WCanon-05（注入分槽隔离）：World Canon 条目只出现在 authoritative.world_canon 槽位；混槽注入被 contract 测试拦截。
- T-WCanon-06（QA 拦截 Canon-as-fact）：叙事含"World Canon 内容 + 完成性表述"时 QA 拦截并记录 CANON_AS_FACT_VIOLATION；重写后仍失败则降级。
- T-WCanon-07（Evolution 提案写入与审核流程）：提案写入 canon_proposal_log（status=pending）；审核后状态变更可回放；canon_change_log 含变更前后 hash。

#### 19.1.6 新增错误码

- CANON_SYNC_FAILED：World Canon 文件扫描/同步失败
- WCANON_SLOT_VIOLATION：World Canon 混槽注入（注入到非 authoritative.world_canon 槽位）
- WCANON_PROPOSAL_INVALID：提案字段不合规（缺 doc_id/body/proposer 等）

---

### 19.2 日记 Evolution 提案关联（FR-DIARY-002）

#### 19.2.1 触发条件

- 日记生成（diary_writer.py）完成后，LLM 输出可选包含 drift_suggestion 字段；
- drift_suggestion 结构：{doc_id, suggested_body, reason（不超过 200 字）}；
- 若 drift_suggestion 存在且字段合规：生成 canon_proposal（proposer=diary_writer，source_intent_id=日记 intent_id）写入 canon_proposal_log；
- 若 drift_suggestion 不存在或字段不合规：跳过（默认路径），不阻塞日记写入。

#### 19.2.2 QA 约束

- drift_suggestion.suggested_body 必须经 QA 校验（不得含伪执行句、不得含完成性表述）；
- 校验失败：丢弃 drift_suggestion，日记正常写入，记录 WCANON_PROPOSAL_INVALID。

#### 19.2.3 审计串联

- canon_proposal_log.source_intent_id 指向日记 intent_id；
- 可从日记 intent_id 追溯到 canon_proposal_log（正向）；
- 可从 canon_proposal_log.proposal_id 追溯到日记 intent_id（反向）。

#### 19.2.4 回归测试编号（新增）

- T-Diary-03（Evolution 提案写入）：日记含 drift_suggestion 时，canon_proposal_log 写入成功，source_intent_id 可追溯到日记 intent_id。
- T-Diary-04（Evolution 提案 QA 拦截）：drift_suggestion.suggested_body 含伪执行句时，提案被丢弃，日记正常写入，WCANON_PROPOSAL_INVALID 可统计。

#### 19.2.5 新增错误码

- DIARY_EVOLUTION_PROPOSAL_SKIPPED：日记无 drift_suggestion 或字段不合规，提案跳过（非错误，仅可观测）

---

### 19.3 M9 新增错误码汇总

| 错误码 | 触发场景 |
|--------|---------|
| CANON_SYNC_FAILED | World Canon 文件扫描/同步失败 |
| WCANON_SLOT_VIOLATION | World Canon 混槽注入 |
| WCANON_PROPOSAL_INVALID | 提案字段不合规（含 QA 拦截） |
| DIARY_EVOLUTION_PROPOSAL_SKIPPED | 日记无有效 drift_suggestion（可观测，非错误） |

---

### 19.4 M9 回归测试编号汇总

| 编号 | 覆盖需求 | 断言口径 |
|------|---------|---------|
| T-WCanon-04 | FR-WCANON-002 | 索引同步幂等性 + CANON_SYNC_FAILED 可统计 |
| T-WCanon-05 | FR-WCANON-003 | 注入分槽隔离（contract 测试） |
| T-WCanon-06 | FR-WCANON-001 + FR-CANON-001 | QA 拦截 Canon-as-fact + 重写/降级 |
| T-WCanon-07 | FR-WCANON-002 | Evolution 提案写入/审核/变更 hash 可回放 |
| T-Diary-03 | FR-DIARY-002 | 提案写入 + source_intent_id 串联 |
| T-Diary-04 | FR-DIARY-002 | 提案 QA 拦截 + 日记正常写入 |
"""

with open('D:/nastume/Natsume_Ai/docs/30_devspec/DevSpec.md', 'a', encoding='utf-8') as f:
    f.write(content)
print('done')
