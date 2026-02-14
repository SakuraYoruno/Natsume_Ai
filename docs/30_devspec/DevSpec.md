受众：CODEX
标题：开发规格 DevSpec — 模板（工程落地）

本文只写“怎么做”与“如何验证”，不写产品愿景。
# Natsume Ai 产品开发文档（Dev Spec）
版本：v1.0
日期：2026-02-14
适用架构：Host_Brain（Python） + Dify（LLM Agent） + Natsume_Body（Edge） +（可选）Cloud Exocortex + ClawdBot

本文件用于“让 Codex 直接开工”，目标是把白皮书与 PRD 的原则/需求落成可实现的工程规格：模块边界、接口、Schema、日志、测试与里程碑。  
注意：本文件不讨论叙事创作与世界观文本；那些属于《白皮书（宪法与人格）》与《理论文档》。

---

## 0. 术语与硬约束（执行口径）
0.1 单向链：Dify → Host Governor → Skills/Actuators → Audit  
0.2 事实：仅以审计链（intent/governor/skill_exec）与硬状态/SSOT 为准。  
0.3 任何执行必须携带 approval_token；高风险必须 confirm_token；可逆变更默认要求 undo_token+manifest。  
0.4 Dify 永远不执行、不裁决、不宣称已执行；所有“已完成/已保存/已确认”只能引用 Host 审计摘要字段。

---

## 1. 代码仓库结构（建议标准）
仓库单一 mono-repo（在 Host 侧用 Codex 生成两端代码并 Git 提交，Edge 拉取）：

- `host_brain/`
  - `app/`
    - `main.py`（HTTP API / CLI 入口）
    - `config.py`
  - `core/`
    - `context_pack.py`（打包：hard_snapshot + bond_summary + retrieval + recent_events）
    - `qa.py`（输出合规：伪执行/语言/Canon-as-fact 等）
    - `metabolism.py`（energy/mood/sys_mode 演化）
  - `governor/`
    - `rules.py`（R1–R7；引用 SkillMeta、snapshot）
    - `schemas.py`（Intent/GovernorOutput/Token/Manifest Pydantic）
    - `decision.py`（裁决入口）
  - `skills/`
    - `registry.py`（SkillMeta 注册）
    - `loader.py`（唯一合法执行入口：token 校验、幂等、审计写入）
    - `impl/`（具体技能实现；只能被 loader import）
  - `bond/`
    - `bond_dynamics.py`（唯一写 Neo4j）
    - `event_sampler.py`
    - `neo4j_client.py`
  - `memory/`
    - `episodic.py`（Chroma 写入/召回）
    - `canon_store.py`（Style Canon 主权存储：文件/向量均可）
  - `audit/`
    - `sqlite_ledger.py`（append-only ledger + 索引表）
    - `logs.py`（JSONL 或 SQLite 表结构封装）
- `edge_client/`
  - `senses/`（ears/eyes）
  - `actuators/`（mouth/speaker/avatar）
  - `rpc/`（Edge gRPC/HTTP 服务端 + Host client）
  - `config.py`
- `dify/`
  - `chatflow/`（导出 JSON / YAML）
  - `prompts/`（仅用于 Dify 的约束提示与 JSON 输出模板）
- `docs/`
  - `whitepaper_constitution_persona.md`
  - `prd.md`
  - `devspec.md`
  - `theory.md`
  - `AGENTS.md`（给 Codex 的工程指令）
- `tests/`
  - `contract/`（Schema/validator/gates）
  - `e2e/`（最小合规测试 T-xx）
  - `fixtures/`（回归样例 intents、bad outputs）

强制：CI 静态扫描禁止旁路 import（见 8.2）。

---

## 2. 运行时进程拓扑与通信
2.1 Host 进程（主权裁决）
- 对外提供：`POST /v1/chat`（用户输入 → 输出 narrative + intent_draft + quality）
- 对内依赖：SQLite、Neo4j、Chroma、（可选）Cloud Exocortex、Edge RPC、Dify HTTP

2.2 Edge 进程（身体/执行）
- 对外提供（给 Host）：`POST /rpc/screenshot`、`POST /rpc/play_audio`、`POST /rpc/ui_action`（若启用 ClawdBot 也可在 Edge 或 Host 侧）

2.3 Dify（Ego）
- Host 调用 Dify：`POST /v1/dify/chat`（封装为 Host 的客户端模块）
- Dify 输出：严格 JSON（narrative + intent_draft），不得调用工具。

2.4 通信安全（最小实现）
- Host→Edge：每次请求带 `request_id` + `HMAC(signature)`（共享密钥）+ `ts_ms` + `nonce`
- Edge 维护 nonce 缓存窗口，拒绝重放（REPLAY_DETECTED）。

---

## 3. Schema：Intent（必须强类型）
### 3.1 Intent JSON Schema（概念）
Intent 是 Host 可裁决的请求对象。Host 只接受一个 JSON 对象（不接受数组/任意 JSON）。

最小字段：
- `schema_version`：`"natsume.intent.v1"`
- `id`：ULID/UUID
- `ts_ms`：int
- `session_id`：string
- `seq`：int（单调递增）
- `type`：enum
- `action`：enum（allowlist）
- `target`：enum（allowlist）
- `args`：object（按 action 强校验）
- `nonce`：string

### 3.2 Pydantic 模型（实现要求）
- 使用 Pydantic v2（或 dataclasses+jsonschema 亦可），但必须提供：
  - `validate_lenient(text)->Intent|None`（宽松解析：只修语法不补语义）
  - `validate_strict(obj)->Intent`（严格校验：字段/类型/枚举/args/重放/时效）
- 宽松解析失败：`INTENT_PARSE_FAILED`
- 严格校验失败（字段/枚举/类型）：`INTENT_SCHEMA_INVALID`
- args 校验失败：`INTENT_ARGS_INVALID`

### 3.3 args 参数契约（每个 action 一份）
每个 action 在 `governor/schemas.py` 内有对应 `ArgsModel`，并在 registry 中绑定：
- required_keys / optional_keys
- 类型、范围、长度、正则
- 路径安全（base_dir 约束，禁止 `..`）
- 域名 allowlist / 分类策略（出站气闸）

---

## 4. Schema：GovernorOutput（Host→上层）
### 4.1 输出字段（必须）
- `decision`：`APPROVE|REJECT`
- `reason_code`：仅当 `decision=REJECT` 必须存在（见白皮书 6.4 枚举）
- `rule_trace`：命中的 Gate 列表（R1–R7）+ 阈值 + 快照摘要
- `tokens`：
  - APPROVE：至少 `approval_token_ref`
  - REJECT：不得下发 `approval_token_ref`
- `snapshot_refs`：hard_state/bond_snapshot 的版本号或 hash

### 4.2 输出断言（运行时必须）
- REJECT 缺 reason_code 或 rule_trace：拦截并记录 `GOVERNOR_OUTPUT_INVALID`
- APPROVE 缺 approval_token_ref：Skills 必须拒绝执行并记录 `SKILL_TOKEN_MISSING`

---

## 5. Token：approval/confirm/undo（票据化执行）
### 5.1 approval_token（短 TTL）
建议字段：
- `token_id`, `intent_id`, `session_id`, `seq`, `skill_name`, `issued_ts_ms`, `expiry_ts_ms`, `nonce`, `sig`
要求：
- token 绑定 `intent_id + skill_name + session_id + seq`
- TTL 默认 60–300 秒（配置项）
- 使用 HMAC-SHA256（单机）或 Ed25519（可选）

### 5.2 confirm_token（高风险二次提交）
- 由 Host 交互流程产生（按钮/二次输入校验 hash）
- 必须绑定 `intent_id + skill_name`
- TTL 更短（例如 60 秒）
- 仅用于 risk_level=4 / irreversible / payment/account/system-settings

### 5.3 undo_token + manifest（可撤销默认）
- reversible skill 执行必须产出 `undo_token_ref` 和 `manifest_ref`
- manifest 必含 `integrity_digest`（sha256）和 `items[]`（before/after/hash/artifact_refs）

---

## 6. Skills：注册、执行、幂等与审计
### 6.1 SkillMeta（强制字段）
字段与白皮书一致：`name, clearance_level, min_trust, energy_cost, risk_level, reversible, scopes, requires_visual_audit, requires_confirm_token, data_classification, allowlist, denylist, rate_limit`

注册方式：
- `skills/registry.py` 中 `register(skill_name, meta, impl_callable)`
- CI 检查：每个 impl 必须有 meta，且 meta 字段完整

### 6.2 执行入口（唯一）
`skills/loader.py: execute(intent, approval_token, confirm_token?, undo_token?) -> SkillResult`

必须做：
- token 验证（签名/过期/绑定一致）
- 幂等：同一 intent_id 若已成功执行，返回缓存结果或拒绝重做（策略配置）
- 写入 `skill_exec_log`（包含 artifacts、undo_ref、result_digest）

### 6.3 ClawdBot（UI 自动化）作为 Skill
- `clawd_ui_action` 必须 `requires_visual_audit=true`
- 每一步写 `clawd_action_log`：before/after 截图引用、target_desc、confidence
- confidence < 0.80：停止并 reason_code=`UI_ELEMENT_NOT_FOUND`
- 红线场景：门禁不足 → `VISUAL_REDLINE`

---

## 7. Context Pack：Host→Dify 的输入契约
Host 打包 context_pack（JSON）给 Dify，字段建议：

- `hard_snapshot`：energy/mood/sys_mode/safety_bucket（不含敏感原值）
- `bond_summary`：stage/trend/capability_hint（不含具体 trust 数值）
- `retrieval`：
  - `episodic`：[{summary_cn,tags,ts,evidence_refs}]
  - `style_canon`：[{kind,triggers,template,constraints}]（tone_only）
- `recent_events`：[{kind,reason_code,ts,summary}]
- `constraints`：
  - `no_false_execution_claim=true`
  - `tone_policy`（短句/……/称呼）
  - `language_policy`（若仍要求日语则写这里；如果你已改为中文口吻则同步修改）
  - `intent_output_schema`：强制输出 JSON 结构

要求：
- style_canon 必须与 facts 分字段隔离
- Dify 不得自行检索，不得扩展召回

---

## 8. Audit：SQLite Ledger 与日志表（最低实现）
### 8.1 推荐表
- `chat_history`：turn_id, ts_ms, user_text, assistant_text, session_id, seq
- `intent_log`：intent_id, raw_text, normalized_json, session_id, seq, ts_ms
- `governor_log`：intent_id, decision, reason_code, rule_trace_json, snapshot_refs_json, token_refs_json, ts_ms
- `skill_exec_log`：intent_id, skill_name, approval_token_ref, result_status, result_code, result_digest, artifacts_json, undo_ref, ts_ms
- `airlock_log`：direction, destination, sanitization_report_json / quarantine_json, ts_ms, request_id
- `clawd_action_log`：step_id, intent_id, before_artifact, after_artifact, action_json, confidence, ts_ms
- `bond_update_log`：event_id, etype, intensity, before_json, after_json, rule_version, evidence_refs_json, ts_ms
- `evolution_log`：proposal_id, before_json, after_json, locks_json, ts_ms
- `error_log`：stage, reason_code, detail_json, ts_ms, request_id

### 8.2 静态扫描规则（CI 必须）
禁止旁路 import：
- 任何非 `skills/loader.py` import `skills/impl/*`
- 任何非 `bond/bond_dynamics.py` import `bond/neo4j_client.py`
- 任何业务层直接调用 Edge RPC（必须封装为 Skill impl）

---

## 9. QA：输出合规模块（Host 必须）
`core/qa.py` 负责对 Dify 输出做“合规检查与重写一次”：
- 伪执行宣告（FALSE_EXECUTION_CLAIM）
- Canon 当事实（CANON_AS_FACT_VIOLATION）
- 非法语言/口吻（LANGUAGE_POLICY_VIOLATION / TONE_POLICY_VIOLATION）
- JSON 结构漂移（LLM_FORMAT_DRIFT）

策略：
- 允许重写 1 次；仍失败 → Host 最小模板输出（并记录 reason_code）

---

## 10. 最小可交付里程碑（MVP→LSP）
### M0（可跑通）
- Host `/v1/chat`：输入→调用 Dify→QA→输出
- Intent 宽松解析 + 严格校验
- Governor REJECT/APPROVE + reason_code + token_ref
- Skills 框架：至少实现 `narrative_only`（不执行）与 `memory_recall`（只读）
- SQLite audit 最小三表：intent/governor/skill_exec

### M1（安全闭环）
- approval_token 验证
- 重放保护：id/seq/ts/nonce
- reason_code 枚举与测试（T-16/T-17）
- CI 静态扫描禁止旁路

### M2（Edge 接入）
- Host↔Edge RPC：screenshot/play_audio
- 失败降级（DEPENDENCY_UNAVAILABLE 等）

### M3（LSP）
- undo_token+manifest
- Artifact Vault + vault_log + GC
- DesktopIntervention（仅 Natsume_Space，可撤销）

---

## 11. 合规测试（从白皮书映射）
Dev Spec 必须提供可执行的测试清单文件（见 `tests/e2e/`），建议按编号直接落地：
- T-14/T-15 Governor 输出契约
- T-18~T-22 Intent 校验与重放
- T-34~T-38 Token 与 confirm/undo
- T-41~T-43 视觉熔断
- T-67~T-71 降级与 QA
- T-72~T-76 生命周期与 SSOT

---

## 12. Codex 执行说明
你在每次让 Codex 改代码前，必须提供：
- 目标里程碑（M0/M1/M2/M3）
- 要改的文件列表（全文件替换优先）
- 需要通过的测试编号（例如 T-14、T-18、T-34）
- 不变量（例如 I1/I8/I9）不可破坏
- 输出格式：必须给出变更摘要 + 变更文件列表 + 下一步建议

（更完整的 Codex 行为规范在 `docs/AGENTS.md`。）


## 13. 实施检查清单（Codex 执行用）

### 13.1 总览（体系结构与数据流）
目标：确保任何实现都不破坏“单向链 + 审计即事实”。

必须清晰表达并在实现中保持一致的事实：
- 单向链：输入 → Dify（narrative + intent_draft）→ Host（Governor 裁决）→ Skills/Actuators（执行）→ Audit（事实）
- “事实来源”：仅审计链（intent/governor/skill_exec）+ Hard State/SSOT；Dify 叙事不构成事实

落地物料（工程侧）：
- 一个可读的“运行时链路图”（可以是 docs 内一个简图段落，不是代码）
- `Host /v1/chat` 的输入输出契约（见本文件第 2、4、7 章）

验收点（最小）：
- 任意“已执行/已保存/已确认”类表达必须来自 Host 审计摘要字段（见第 0、9 章）


### 13.2 模块边界（可静态扫描验证）
目标：把“谁能做什么”写死到工程结构里，避免旁路。

边界定义（必须满足）：
- Host：Hard State / Governor / RAG 主权 / Audit / Bond Dynamics / Evolution
- Dify：只负责叙事生成 + Intent 草案（禁止执行、禁止裁决、禁止宣称事实）
- Edge：感知/执行端（RPC + 签名 + 重放保护），不得有裁决逻辑

落地物料（工程侧）：
- 静态扫描规则清单（CI 失败条件），必须覆盖：
  - 非 `skills/loader` 直接 import `skills/impl`
  - 非 `bond_dynamics` 直接使用 Neo4j 写入能力
  - 业务层绕过 Skills 直连 Edge RPC

验收点（最小）：
- CI 中“旁路 import”用例必须能触发失败（对应第 8.2）


### 13.3 协议与 Schema（强类型 + 可回放）
目标：把“语言 → 行为”的唯一入口限定为强类型对象，并能稳定拒绝、稳定审计、稳定回放。

必须存在的契约（工程必备）：
- Intent：
  - lenient parse（只修语法不补语义）
  - strict validate（字段/类型/枚举/args/重放/时效）
  - action/target/type allowlist
- Governor 输出：
  - `decision/reason_code/rule_trace/tokens/snapshot_refs` 结构固定
  - REJECT 必带 reason_code + rule_trace
  - APPROVE 必带 approval_token_ref
- 审计日志：
  - 最少 intent_log / governor_log / skill_exec_log 三表（或等价 JSONL）

落地物料（工程侧）：
- Intent / GovernorOutput / Token / Manifest 的结构化定义（Pydantic 或等价）
- reason_code 枚举的引用位置与使用约束（统一入口）

验收点（最小）：
- T-14/T-15（Governor 输出契约）
- T-18~T-22（Intent 校验与重放）


### 13.4 关键规则与算法（可版本化、可回归）
目标：把裁决与授权的关键逻辑做成“版本化规则”，而不是散落在业务代码里。

必须实现且可版本化的规则族：
- Governor Gates（R1–R7）：
  - trust / energy / sys_mode / rate_limit / scope_policy / safety / airlock
- Token 体系：
  - approval_token：绑定 intent_id + skill_name + session_id + seq + TTL
  - confirm_token：高风险二次提交证明
  - undo_token + manifest：可逆默认（LSP）

落地物料（工程侧）：
- 每条 Gate 的阈值与命中输出必须可追溯到 rule_trace（规则版本号建议写入 trace）
- Token 的绑定字段与校验规则必须列为“不可破坏约束”（写在 DevSpec 票据章节即可）

验收点（最小）：
- T-34~T-38（Token/confirm/undo 强制与校验）


### 13.5 失败与降级（状态机 + 一次重写）
目标：链路任何阶段坏掉，系统也必须输出“稳定结构 + 明确 reason_code”，且绝不编造事实。

必须有的机制：
- 分阶段状态机（S0~S8）+ 每阶段：
  - 成功条件 / 失败条件 / 降级策略 / 记录位置 / reason_code
- QA 模块：
  - 伪执行宣告 / Canon-as-fact / 语言口吻 / JSON 漂移
  - 允许重写一次，失败则落 Host 最小模板库

落地物料（工程侧）：
- “降级原因可观测”：error_log 或 quality 字段必须能看到阶段与 reason_code
- “模板库触发条件”：避免误触（见你第 9、26 章的原则已写得很像了）

验收点（最小）：
- T-67~T-71（降级与 QA）


### 13.6 验收测试落地方式（T-xx → tests/contract + tests/e2e）
目标：把白皮书的 T-xx 变成可执行测试集合，作为 Codex 每次改动的“必过门”。

建议分层：
- contract tests：schema、validator、token 校验、旁路扫描（快速、无外部依赖）
- e2e tests：最小链路（Host → Dify stub → Governor → Skill stub → Audit）

必须覆盖的最小集合：
- T-14/T-15
- T-18~T-22
- T-34~T-38
- T-41~T-43（若启用 UI/Clawd）
- T-67~T-71
- T-72~T-76（当你开始做生命周期与 SSOT 严格性时）

落地物料（工程侧）：
- `tests/e2e/TESTPLAN.md`（只列“编号 → 用例 → 断言点 → 触发方式”，不需要代码）
- `fixtures/`（最小 intents、坏输出样例）
