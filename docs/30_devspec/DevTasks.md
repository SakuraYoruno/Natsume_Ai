受众：CODEX
标题：DevTasks（任务清单：唯一可执行任务来源）

版本：v1.8
日期：2026-03-04

本文件只包含“Codex 可执行任务条目”。任务条目必须来自 DevSpec 的工程契约拆解，并反向引用 PRD 的 FR 编号与测试编号 T-…；不得在本文件中讨论白皮书/PRD 的文字解释。

## 0. 任务列表与其它文档的关系（强制）

白皮书：法律与禁令，不产出任务。
PRD：需求与验收，不产出任务。
DevSpec：工程契约与验证口径，是任务拆解的唯一来源。
DevTasks：任务卡清单，是 Codex 的唯一工作入口。
Trace：审阅覆盖关系，不作为任务来源。

### 1. 编号规范

需求编号（PRD）：FR-域-编号（例：FR-GOV-003）
测试编号（DevSpec/TESTPLAN）：T-编号（例：T-18）
任务编号（DevTasks）：TASK-四位数字（例：TASK-0123）

### 2. 任务卡格式（每条必须完整）

TASK-XXXX 标题
任务描述：一句话说明要达成的工程结果（不写命令，不写工作流指挥）。
输入：已有模块/既有数据结构/既有文档章节。
输出：新增/修改的接口、Schema、日志点、测试点、文档更新点。
验收标准：必须可验证，优先引用 T-编号；并写明关键断言点（字段/拒绝码/审计记录）。
涉及文件路径：列出将触达的路径（允许新增文件路径，但不写任何命令）。
关联需求/测试：FR-…；T-…；（可选）不变量 I-…

---

## 3. 任务清单（从空目录开始）

### TASK-0000 仓库骨架落地（从空目录开始：目录树+占位文件+索引入口）
- 任务描述：把 DevSpec 目录骨架与关键入口文件落到仓库中，确保后续任务有稳定落点；并保证“无 secrets 也可进入冷启动（M0）开发态”的配置占位存在。
- 输入：DevSpec 1.1（目录树）、DevSpec 1.4（目录职责红线）、DevSpec 1.2.1（冷启动策略）。
- 输出：
  - 缺失目录与关键占位文件；
  - Flow 指针文件（flows/active.pointer）；
  - Flow Trace 索引（docs/60_trace/flows/index.md）；
  - TESTPLAN 占位（tests/TESTPLAN.md）；
  - secret 模板文件（例如 .env.example 或等价模板文件：只包含变量名与示例，不含真实密钥），用于支撑“未配置 API Key 的冷启动形态”。
- 验收标准：
  - 目录与文件存在性可审阅；
  - active.pointer 为单个合法 JSON，包含 dev/staging/prod 三键；
  - flows/index.md 含按环境行；
  - secret 模板文件不包含真实密钥，且能表达“未配置 key 时系统进入降级/替代路径”的配置口径（不要求可用性结论，但要求口径明确）。
- 涉及文件路径：flows/exports/；flows/active.pointer；docs/60_trace/flows/index.md；tests/TESTPLAN.md；prompts/llm_contract/；host_brain/；edge_client/；tools/**；（可选）.env.example。
- 关联需求/测试：FR-FLOW-001；FR-CORE-004；T-Flow-01、T-Flow-02。

### TASK-0001 测试骨架：contract/e2e/fixtures/TESTPLAN 的最小闭环
- 任务描述：建立 tests/contract、tests/e2e、tests/fixtures 的结构与最小断言模板，确保后续每个任务都有可挂载的测试落点；并补齐冷启动所需的 fixtures（无 key / 依赖缺失 / 输出仍稳定）。
- 输入：DevSpec 11（合规测试口径）、DevSpec 1.1（tests 目录约定）、DevSpec 1.2.1（冷启动策略）。
- 输出：
  - TESTPLAN 编号表模板；
  - contract 测试占位（按 Schema/枚举/指针一致性分组）；
  - e2e 最小链路测试占位；
  - fixtures（good/bad intent、bad llm output、replay logs）目录结构；
  - 冷启动 fixtures：至少包含 “无 API Key/外部依赖不可用” 的样例输入与期望输出结构（重点是 stage 与 reason_code 口径，而不是执行成功）。
- 验收标准：
  - TESTPLAN 中存在编号段落（T-Flow/T-Intent/T-LLM/T-Audit/T-QA 等至少列出占位条目）；
  - fixtures 目录具备最小样例文件（可为空内容但命名与字段结构明确）；
  - 冷启动 fixtures 体现：输出仍为单个合法 JSON、且具备 quality.stage 与 reason_code（当适用）。
- 涉及文件路径：tests/TESTPLAN.md；tests/contract/；tests/e2e/；tests/fixtures/**。
- 关联需求/测试：FR-CORE-002；FR-CORE-004；T-Flow-01~02（至少落占位）；T-14、T-15（至少落占位）；T-LLM-01（至少落占位）。

#### TASK-0002 Flow 资产：exports 归档 + active.pointer + Trace 索引一致性
- 任务描述：把 Flow 资产作为可部署配置资产纳入版本化与可追溯闭环，并固化“指针—索引一致性”的回归断言。
- 输入：DevSpec 1.5；PRD FR-FLOW-001。
- 输出：flows/exports 归档约定（目录命名/版本标签）；active.pointer JSON 契约；docs/60_trace/flows/index.md 的一致性规则；fixtures（示例指针与索引配对）。
- 验收标准：T-Flow-01（指针合法性/存在性）、T-Flow-02（索引一致性）可被断言；任意环境可从 active.pointer 与 index.md 唯一确定“当前生效 Flow 资产”。
- 涉及文件路径：flows/exports/；flows/active.pointer；docs/60_trace/flows/index.md；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-FLOW-001；T-Flow-01~02。

#### TASK-0003 LLM 输出契约：Strict JSON + 输出字段约束 + lint + fixtures
- 任务描述：固化“LLM→Host”的 Strict JSON 输出契约，并提供 lint 与 good/bad fixtures，作为 QA 与回归的共同输入源。
- 输入：DevSpec 0.4、DevSpec 9（QA）、PRD FR-CORE-002~003。
- 输出：核心输出契约（narrative、intent_draft、meta）；lint 规则（单 JSON、字段存在性、禁止前后缀）；fixtures（good/bad：多段 JSON/夹杂文本/缺字段/伪执行句）。
- 验收标准：T-LLM-01（输出可解析性与 lint 规则）通过；bad fixtures 均被判定为不合规并产生可统计 violation。
- 涉及文件路径：prompts/llm_contract/core_output_contract.json；host_brain/core/qa.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-CORE-002~003；T-LLM-01；I13。

#### TASK-0004 ContextPack 契约：分槽隔离 + world/style/episodic 口径 + tool_meta_pack 预留
- 任务描述：固化 Host→LLM 的 context_pack 分槽结构，确保事实/权威内容/风格/线索严格隔离，并预留 tool_meta_pack（仅元数据）。
- 输入：DevSpec 7；PRD FR-CANON-001、FR-WCANON-001~003、FR-BOND-003。
- 输出：context_pack 字段结构口径（hard_snapshot/bond_summary/authoritative/retrieval/style_pack/constraints/tool_meta_pack）；混槽判定规则；fixtures（混槽/越权引用/Canon-as-fact）。
- 验收标准：T-CP-01（分槽隔离）通过；混槽输入触发 QA/contract 拦截；Bond Summary 不含数值。
- 涉及文件路径：host_brain/core/context_pack.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-CANON-001；FR-WCANON-001~003；FR-BOND-003；T-CP-01。

#### TASK-0005 Intent 契约：宽松解析 + 严格校验（含重放/顺序/过期）
- 任务描述：形成可复用的 Intent 解析与校验契约，确保“只修语法不补语义”，并把校验失败稳定映射为 reason_code，可审计回放。
- 输入：DevSpec 3；PRD FR-INTENT-001~003。
- 输出：Intent Schema/校验接口落点；失败→reason_code 映射口径；审计字段（session_id/seq/nonce/ts_ms）记录口径；fixtures（good/bad）。
- 验收标准：T-18~T-22 通过；同一规则版本下同类失败 reason_code 稳定；governor_log 可定位失败类型（REPLAY/SEQ/EXPIRED）。
- 涉及文件路径：host_brain/governor/schemas.py；host_brain/governor/decision.py；host_brain/core/ids.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-INTENT-001~003；T-18~T-22；I7。

#### TASK-0006 Intent responsibility：intent_log（proposal）字段补齐（proposer 元信息 + tool_visibility_hash）
- 任务描述：把“谁提议了什么”固化为 intent_log 最小字段集，并保证与 governor_log/skill_exec_log 分离。
- 输入：DevSpec（intent_log 契约）、PRD FR-AUDIT-001（Triptych）、PRD（Intent responsibility 需求条目若已加入）。
- 输出：intent_log 字段口径（proposer_node_id/model_snapshot/prompt_profile_id/tool_visibility_hash/proposed_intent_draft/lint_result）；fixtures（多 proposer 并行 A/B）；回放串联键（request_id/thread_id/turn_id）。
- 验收标准：T-Intent-01（字段最小集）与 T-Intent-02（proposal/decision/exec 分离）通过；intent_log 不能支撑“已执行”叙事。
- 涉及文件路径：host_brain/audit/sqlite_ledger.py；host_brain/core/ids.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-AUDIT-001；T-Intent-01~02。

#### TASK-0007 GovernorOutput 契约：REJECT 必带 reason_code + rule_trace
- 任务描述：固化对外裁决输出契约（decision/reason_code/rule_trace/tokens/snapshot_refs），并定义缺字段输出的合规拦截口径。
- 输入：DevSpec 4；PRD FR-GOV-001~003、FR-CORE-002；白皮书拒绝码约束。
- 输出：GovernorOutput Schema 落点；REJECT/APPROVE 必备字段断言口径；GOVERNOR_OUTPUT_INVALID/ SKILL_TOKEN_MISSING 的触发与记录口径；contract fixtures。
- 验收标准：T-14、T-15、T-16、T-17 通过；对外不出现“缺 reason_code 的 REJECT”；同类拒绝在同版本下 reason_code 稳定可统计。
- 涉及文件路径：host_brain/governor/schemas.py；host_brain/governor/decision.py；host_brain/core/errors.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-GOV-001~003；FR-CORE-002；T-14~T-17。

#### TASK-0008 Governor Gates：R1–R7 骨架 + rule_trace 稳定性
- 任务描述：把 Trust/Energy/SysMode/RateLimit/Scope/Safety/Airlock Gate 的输入输出与 rule_trace 口径固化为可回归规则族。
- 输入：DevSpec 6（Governor rules）、PRD FR-GOV-001。
- 输出：每个 Gate 的最小输入（快照字段）与输出（命中/阈值摘要/原因）；rule_trace 结构字段；fixtures（命中/未命中、边界值）。
- 验收标准：T-Gov-01（rule_trace 可定位阈值与快照摘要）通过；同输入同版本 rule_trace 稳定。
- 涉及文件路径：host_brain/governor/rules.py；host_brain/governor/decision.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-GOV-001；T-Gov-01。

#### TASK-0009 Token 体系：approval/confirm/undo 的绑定与失效口径
- 任务描述：定义并固化票据字段绑定、TTL/过期、签名/校验失败的稳定拒绝码口径，确保“语言确认”不构成授权。
- 输入：DevSpec 5；PRD FR-TOKEN-001~003。
- 输出：Token/Manifest Schema；绑定字段口径（intent_id/skill_name/session_id/seq）；confirm_token/undo_token 的触发条件与拒绝码口径；fixtures（串用/过期/伪造）。
- 验收标准：T-34~T-38 通过；缺 confirm_token 的高风险请求稳定得到 CONFIRM_REQUIRED；不合法/过期/串用 token 稳定得到 TOKEN_INVALID（或更精确子类）。
- 涉及文件路径：host_brain/governor/tokens.py；host_brain/governor/schemas.py；host_brain/governor/decision.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-TOKEN-001~003；T-34~T-38；I6/I11。

#### TASK-0010 Skills 单入口：Registry+Loader 强制 token 校验 + 幂等 + evidence_ref
- 任务描述：固化“唯一合法执行入口”为 loader，并定义幂等与审计写入最小契约，保证执行事实可串联回放。
- 输入：DevSpec 6/8.1；PRD FR-SKILL-001~003、FR-AUDIT-001。
- 输出：SkillMeta 强制字段口径；loader 执行输入输出契约；幂等策略（同 intent_id 不重复副作用）的证明口径；skill_exec_log 字段最小集（含 evidence_ref/manifest_ref）。
- 验收标准：T-Skill-01、T-15、T-34~T-38 通过；任意 skill_exec 均可关联 governor_log + approval_token_ref；重复 intent_id 不产生第二次真实副作用且可由日志证明。
- 涉及文件路径：host_brain/skills/registry.py；host_brain/skills/loader.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-SKILL-001~003；FR-AUDIT-001；T-Skill-01；T-15；I1/I8。

#### TASK-0011 SQLite Ledger：Triptych 三联画最小表 + 索引串联口径
- 任务描述：固化“审计即事实”的最小可回放数据面：intent_log/governor_log/skill_exec_log 的字段口径与串联键。
- 输入：DevSpec 8.1；PRD FR-AUDIT-001。
- 输出：三表字段最小集与串联键口径（request_id/intent_id/token_ref）；error_log/qa_violation_log 口径；回放索引键（event_id/evidence_refs）。
- 验收标准：T-Audit-01、T-14、T-15 通过；任意 request_id 能串联到 intent→governor→(可选)skill_exec；缺链路时可定位到 stage 与 reason_code。
- 涉及文件路径：host_brain/audit/sqlite_ledger.py；host_brain/core/ids.py；host_brain/governor/decision.py；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-AUDIT-001；T-Audit-01；T-14、T-15。

#### TASK-0012 QA 合规模块：Strict JSON + 伪执行宣告 + Canon-as-fact + 格式漂移（一次重写/降级）
- 任务描述：定义 Host 侧 QA 的合规检查与“一次重写/降级”契约，确保对外输出始终可解析且不伪造执行事实。
- 输入：DevSpec 9/7；PRD FR-CORE-002~003、FR-AUDIT-003、FR-CANON-001、FR-WCANON-001~003。
- 输出：违规类型枚举与记录口径（FALSE_EXECUTION_CLAIM、CANON_AS_FACT_VIOLATION、LLM_FORMAT_DRIFT 等）；最小模板降级触发条件与输出结构口径；fixtures（good/bad）。
- 验收标准：T-67~T-71、T-Canon-01~02、T-WCanon-01~03 通过；故障/超时/漂移下仍为单个合法 JSON；完成性表述无证据必拦截。
- 涉及文件路径：host_brain/core/qa.py；host_brain/core/context_pack.py；host_brain/audit/sqlite_ledger.py；prompts/llm_contract/refusal_templates.md；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-CORE-002~003；FR-AUDIT-003；FR-CANON-001；FR-WCANON-001~003；T-67~T-71；I13。

#### TASK-0013 Style Canon / World Canon：版本化、变更留痕、注入口径与回归集
- 任务描述：固化 Canon（style/world）的写入边界、变更留痕与注入口径，确保 Canon 可引用但不越权为事实。
- 输入：DevSpec 8.4/8.5；PRD FR-CANON-001~002、FR-WCANON-001~003。
- 输出：canon_change_log / canon_proposal_log 最小字段；style/world 注入分槽规则；fixtures（Canon-as-fact、引用口径、版本漂移）。
- 验收标准：T-Canon-01~02、T-WCanon-01~03 通过；任何 Canon 更新均可定位到 proposal 与变更前后 hash/version；引用不得变成执行事实。
- 涉及文件路径：host_brain/memory/canon_style_store.py；host_brain/memory/canon_world_store.py；host_brain/memory/canon_sync.py；host_brain/audit/sqlite_ledger.py；host_brain/canon/style/**；host_brain/canon/world/**；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-CANON-001~002；FR-WCANON-001~003；T-Canon-01~02；T-WCanon-01~03。

#### TASK-0014 Episodic：写入门禁（evidence_refs）+ 生命周期（merge/decay/delete）审计
- 任务描述：固化 Episodic 写入必须带 evidence_refs 的门禁与生命周期操作日志，避免幻觉固化。
- 输入：DevSpec 8.3；PRD FR-MEM-001~002。
- 输出：memory_ops_log 字段最小集；写入/合并/衰减/删除的审计事件口径；fixtures（无证据写入、合并后索引）。
- 验收标准：T-Mem-01~02 通过；无 evidence_refs 写入失败且可解释；生命周期操作均可回放。
- 涉及文件路径：host_brain/memory/episodic.py；host_brain/memory/chroma_client.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-MEM-001~002；T-Mem-01~02。

#### TASK-0015 Vault：manifest/undo/GC 的审计闭环（不破坏 undo 窗口）
- 任务描述：固化 Vault 的 manifest 索引、undo 绑定与 GC 审计规则，确保可撤销能力可回放。
- 输入：DevSpec 8.6；PRD FR-LSP-001~002、FR-TOKEN-003。
- 输出：vault_manifest_index / vault_gc_log 字段最小集；GC 不破坏 undo 窗口的断言口径；fixtures（manifest 缺失/ digest 不匹配）。
- 验收标准：T-Vault-01~02 通过；reversible=true 的执行必须产出 manifest_ref；manifest digest 不匹配时撤销拒绝并可审计。
- 涉及文件路径：host_brain/lsp/vault.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-LSP-001~002；FR-TOKEN-003；T-Vault-01~02；I11/I12。

#### TASK-0016 Airlock：出站净化报告 + 入站隔离分类（不得直通执行）
- 任务描述：固化 AIRLOCK 的净化报告、入站隔离分类与审计字段口径，确保外部内容永远降权为草案。
- 输入：DevSpec 8.7；PRD FR-AIRLOCK-001~002。
- 输出：airlock_log 字段最小集（removed_fields/redactions/payload_digest/quarantine_digest）；入站分类枚举（FACT/DRAFT_CODE/DRAFT_INSTRUCTION/UNKNOWN）；fixtures（未净化/直通尝试）。
- 验收标准：T-39~T-40 通过；缺净化报告的出站必须拒绝并记录 AIRLOCK_BLOCKED_EGRESS；入站内容不得直接触发执行。
- 涉及文件路径：host_brain/airlock/sanitizer.py；host_brain/airlock/quarantine.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-AIRLOCK-001~002；T-39~T-40；I10。

### TASK-0017 Tool Catalog：工具元数据 SSOT（仅元数据）+ Top-K 选择 + tool_visibility_hash
- 任务描述：建立工具目录的元数据 SSOT 与 Top-K 候选选择，确保模型每轮只见候选工具子集，并可产出 tool_visibility_hash。
- 输入：DevSpec 8.9（Tools：Tool Catalog/Selector/Broker）；PRD FR-TOOL-001~002。
- 输出：ToolMeta 字段最小集（id/name/desc/schema_hash/side_effect_level/auth_level/domain_tags/version）；Top-K 输出结构；tool_visibility_hash 计算口径；fixtures（包含 READ/WRITE 混合工具）。
- 验收标准：T-Tool-01（仅元数据/字段完整性）、T-Tool-02（Top-K 子集/WRITE 默认不可见）通过；全量工具不暴露给 LLM。
- 涉及文件路径：host_brain/tools/catalog.py；host_brain/tools/selector.py；host_brain/core/context_pack.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-TOOL-001~002；T-Tool-01~02；I14。

#### TASK-0018 ToolBroker：单入口执行 + WRITE token gating + evidence_ref 串联
- 任务描述：固化所有副作用工具执行的唯一入口与证据锚点（evidence_ref），并把 WRITE 工具纳入 capability/approval 票据约束。
- 输入：DevSpec 8.9（ToolBroker）；PRD FR-TOOL-003；FR-TOKEN-001。
- 输出：ToolExec 输入输出契约；WRITE 拒绝码口径（TOKEN_INVALID/CONFIRM_REQUIRED/…）；skill_exec_log/evidence_ref 串联规则；fixtures（无 token 调用 WRITE）。
- 验收标准：T-Tool-03 通过；无 token 的 WRITE 必拒绝且可统计；任何“已执行”叙事可指向 evidence_ref。
- 涉及文件路径：host_brain/tools/broker.py；host_brain/audit/sqlite_ledger.py；host_brain/core/errors.py；tests/contract/；tests/fixtures/；tests/e2e/。
- 关联需求/测试：FR-TOOL-003；T-Tool-03；I14。

#### TASK-0019 MCP：provider 元数据同步 + 受治理调用（READ/WRITE 分级）
- 任务描述：把 MCP 工具提供方纳入 Tool Catalog，并确保调用受 token 治理与审计串联约束。
- 输入：DevSpec 8.10（MCP）；PRD FR-MCP-001。
- 输出：MCP provider→ToolMeta 映射；READ/WRITE 分级口径；审计字段（provider_id/tool_id/request_id/evidence_ref）。
- 验收标准：T-MCP-01 通过；MCP WRITE 无 token 必拒绝；所有 MCP 调用可回放到 intent/governor/evidence_ref。
- 涉及文件路径：host_brain/tools/mcp_adapter.py；host_brain/tools/catalog.py；host_brain/tools/broker.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-MCP-001；T-MCP-01；I14。

#### TASK-0020 通道：微信 I/O 事件化 + channel_io_log 串联
- 任务描述：把微信收发作为确定性通道能力事件化，并写入审计链，禁止由 LLM 自由决定“是否发送/发给谁”。
- 输入：DevSpec 8.11.1（微信通道）；PRD FR-CHANNEL-001。
- 输出：wechat 入站/出站事件结构；channel_io_log 字段最小集（thread_key/message_id/request_id/ts/status）；fixtures（重复消息/失败回执）。
- 验收标准：T-Channel-01 通过；任意入站/出站可追溯到 thread_key 与 request_id；发送失败可解释（reason_code/stage）。
- 涉及文件路径：host_brain/channels/wechat.py；host_brain/audit/sqlite_ledger.py；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-CHANNEL-001；T-Channel-01。

#### TASK-0021 外显：VTube Studio talk gate + emotion_tag→expression_id 固定映射
- 任务描述：固化“说话开始/结束”与“表情枚举映射”的确定性行为，拒绝模型自由动作参数。
- 输入：DevSpec 8.11.2（VTS 外显）；PRD FR-AVATAR-001。
- 输出：emotion_tag 枚举与 expression_id 映射表；审计字段口径（emotion_tag/expression_id/trigger）；fixtures（非法 tag、自由动作尝试）。
- 验收标准：T-Avatar-01 通过；任何表情触发可回放到固定映射；自由动作参数输入被拒绝并留痕。
- 涉及文件路径：host_brain/channels/vts.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-AVATAR-001；T-Avatar-01。

#### TASK-0022 ClawdBot UI Skill：before/after 证据 + 置信度门槛 + 红线熔断
- 任务描述：固化 UI 自动化的证据链与红线熔断口径，确保低置信度不点击、红线场景不越权。
- 输入：DevSpec 6.3（ClawdBot 作为 Skill）；PRD FR-VISION-001~002、FR-AUDIT-002。
- 输出：clawd_action_log 字段最小集（before/after artifact/confidence/target_desc）；UI_ELEMENT_NOT_FOUND 与 VISUAL_REDLINE 的触发口径；fixtures（低置信度/红线页面）。
- 验收标准：T-41~T-43 通过；confidence<0.80 时不发生动作且写日志；红线场景门禁不足必拒绝并可审计。
- 涉及文件路径：host_brain/skills/impl/clawd_ui_action.py；host_brain/audit/sqlite_ledger.py；host_brain/lsp/vault.py；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-VISION-001~002；FR-AUDIT-002；T-41~T-43。

### TASK-0023 服务项清单落地：服务注册表 + health 口径 + 降级可观测
- 任务描述：为所有“必须实现”的服务项建立统一注册表与 health 契约，保证服务即使暂不可用也能被识别、被降级、被审计。
- 输入：PRD（服务必须实现口径）；DevSpec 1.2.1/2（进程拓扑）/QA（降级结构）。
- 输出：服务注册表字段口径（service_id/owner(host|edge)/required=true/status）；health 输出结构；不可用→reason_code/stage 映射口径；fixtures（服务不可用矩阵）。
- 验收标准：同版本下同类不可用 reason_code 稳定；输出结构可解析；审计链可回放到 request_id。
- 涉及文件路径：host_brain/core/；host_brain/audit/；edge_client/rpc/；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-CORE-004；FR-OBS-001；T-67~T-71（复用）；（新增）T-Svc-01。

### TASK-0024 Edge RPC：语音链路端点与事件 Schema（ASR/TTS/打断）
- 任务描述：补齐 Edge 语音链路的 RPC 端点与事件 Schema，使 Host 能以事件驱动编排语音输入与播报输出。
- 输入：DevSpec 8.11.3（VOICE 契约）；PRD FR-VOICE-001~003。
- 输出：asr_chunk/speak/barge_in 的请求响应字段口径；事件上送节流与顺序口径；失败 reason_code/stage 口径；fixtures（字段缺失/乱序/低置信度）。
- 验收标准：T-Voice-01~T-Voice-03 通过；事件结构稳定；可回放。
- 涉及文件路径：edge_client/rpc/；edge_client/senses/；edge_client/actuators/；tests/contract/；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-VOICE-001~003；T-Voice-01~03。

### TASK-0025 Edge ASR（large-v3）：上送事件稳定化与 alternates（可选字段但必须支持）
- 任务描述：将 ASR 输出稳定为 asr_chunk 事件结构，并支持 alternates 字段用于检索提示；低置信度显式标注。
- 输入：DevSpec 8.11.3；PRD FR-VOICE-001。
- 输出：asr_chunk 最小字段集；alternates 字段口径；低置信度标注策略；审计串联键。
- 验收标准：T-Voice-01 通过；低置信度场景仍结构稳定且可回放。
- 涉及文件路径：edge_client/senses/；edge_client/rpc/；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-VOICE-001；T-Voice-01。

### TASK-0026 Edge TTS（GPT-SoVITS 全 GPU 常驻）：合成与播报队列、失败口径
- 任务描述：形成 speak→合成→播报队列的稳定能力与失败可解释口径，确保强交互下可被打断且不伪执行。
- 输入：DevSpec 8.11.3；PRD FR-VOICE-002。
- 输出：播报队列事件结构；失败 reason_code/stage 口径；与 request_id 串联字段。
- 验收标准：T-Voice-02 通过；失败可解释且不影响 Host 输出结构。
- 涉及文件路径：edge_client/actuators/；edge_client/rpc/；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-VOICE-002；T-Voice-02。

### TASK-0027 Barge-in（强交互可打断）：打断策略与审计事件
- 任务描述：落实“用户开口→打断播报→优先 ASR”的策略与事件化审计，且打断不构成执行事实。
- 输入：DevSpec 8.11.3；PRD FR-VOICE-003。
- 输出：barge_in 触发条件口径；打断事件字段；Host 审计记录点。
- 验收标准：T-Voice-03 通过；打断立即生效；ASR 不被阻塞；审计可回放。
- 涉及文件路径：edge_client/actuators/；edge_client/senses/；host_brain/audit/；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-VOICE-003；T-Voice-03。

### TASK-0028 Edge 兜底 LLM（Ollama 3B 量化，默认卸载）：draft-only 端点与快速失败回退
- 任务描述：补齐 Edge 兜底模型端点（presence/asr_compile/retrieval_hints/context_compress），强制 draft_only=true；不可用时快速失败并回退 Host 兜底口径。
- 输入：DevSpec 8.11.3；PRD FR-VOICE-004。
- 输出：端点输入输出字段；禁止裁决字段与禁止执行事实宣告约束；不可用错误码口径。
- 验收标准：T-Voice-04 通过；任何兜底输出均不含裁决/执行事实宣告；不可用可统计。
- 涉及文件路径：edge_client/rpc/；edge_client/senses/；host_brain/core/qa.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-VOICE-004；T-Voice-04。

### TASK-0029 Edge 资源闸门：ASR>播放>TTS合成>兜底LLM 的互斥与可观测
- 任务描述：落实资源闸门的优先级与互斥策略，避免峰值叠加导致不稳定，并提供可观测状态。
- 输入：DevSpec 8.11.3；PRD FR-VOICE-005。
- 输出：闸门状态机口径；抢占/拒绝的 reason_code/stage；资源指标字段（显存/队列长度）。
- 验收标准：T-Voice-05 通过；压力场景优先保证 ASR；降级行为可回放。
- 涉及文件路径：edge_client/actuators/；edge_client/rpc/；edge_client/proofs/；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-VOICE-005；T-Voice-05。

### TASK-0030 Host 检索链：Embedding 服务接口与降级口径
- 任务描述：补齐 embedding 服务接口、健康状态与不可用降级口径（不得伪装为“记忆为空”）。
- 输入：DevSpec 8.3.1；PRD FR-MEM-003。
- 输出：embedding 输出结构；不可用/超时→reason_code/stage 映射；审计串联点。
- 验收标准：T-Ret-01 通过；结构稳定；reason_code 可统计。
- 涉及文件路径：host_brain/retrieval/；host_brain/audit/；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-MEM-003；T-Ret-01。

### TASK-0031 Host 检索链：Reranker 服务接口与降级口径
- 任务描述：补齐 reranker 服务接口、健康状态与不可用降级口径，并保持 evidence pack 结构稳定。
- 输入：DevSpec 8.3.1；PRD FR-MEM-004。
- 输出：rerank 输出结构；不可用降级策略口径；审计串联点。
- 验收标准：T-Ret-02 通过；evidence pack 仍可生成且字段稳定。
- 涉及文件路径：host_brain/retrieval/；host_brain/audit/；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-MEM-004；T-Ret-02。

### TASK-0032 Host 召回融合：Evidence Pack 结构与回放引用
- 任务描述：补齐召回融合输出的 evidence pack 结构，使回答与审计可引用同一证据包。
- 输入：DevSpec 8.3.1；PRD FR-MEM-005。
- 输出：evidence pack 最小字段与序列化口径；与 request_id/intent_id 串联字段。
- 验收标准：T-Ret-03 通过；字段结构稳定；可回放引用。
- 涉及文件路径：host_brain/retrieval/；host_brain/audit/；tests/contract/；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-MEM-005；T-Ret-03。

### TASK-0033 Host 屏幕监控与 OCR（按需）：Gaming 门控 + 结构化输出
- 任务描述：补齐屏幕监控与 OCR/识图的按需触发能力，并落实 Gaming 不触发门控；输出必须结构化且带 source_ref。
- 输入：PRD FR-VISION-003；DevSpec（新增门控与结构化输出契约点需补齐到 vision 章节）。
- 输出：OCR 输出结构（text_blocks/bbox/conf/source_ref）；Gaming 门控拒绝码口径；审计串联字段。
- 验收标准：Gaming 下稳定拒绝/降级；非 Gaming 下结构稳定且可回放。
- 涉及文件路径：host_brain/vision/；host_brain/audit/；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-VISION-003；（新增）T-VisionGate-01、T-Vision-01。

### TASK-0034 Host 摄像头识别：事件化输出与 evidence_ref
- 任务描述：补齐摄像头识别链路的事件化输出（objects/events + confidence + evidence_ref），并与审计链串联。
- 输入：PRD FR-VISION-004；DevSpec（vision 事件化契约点需补齐到 vision 章节）。
- 输出：camera_event 结构；证据引用口径；低置信度标注策略。
- 验收标准：事件可回放并指向 evidence_ref；证据缺失时稳定降级/拒绝。
- 涉及文件路径：host_brain/vision/；host_brain/lsp/；host_brain/audit/；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-VISION-004；（新增）T-Vision-02。

### TASK-0035 Host Bond SSOT（Neo4j）：单写者写入边界 + bond_update_log + 约束/索引
- 任务描述：补齐 Neo4j Bond SSOT 的“单写者”写入边界与最小图模型落点（含约束/索引），并把写入事实以 bond_update_log（或等价）方式串联到审计链，确保可回放与可静态扫描验证。
- 输入：DevSpec 8.2（Bond SSOT）、DevSpec 8.8（单写者静态扫描断言）；PRD FR-BOND-001~003。
- 输出：
  - Bond 写入唯一入口与字段口径（event_id/evidence_refs 最小集）；
  - Neo4j 约束/索引的声明性资产（migrations 或等价目录）；
  - bond_update_log（或等价日志）与 SQLite Ledger 串联键口径；
  - fixtures：包含“非单写者尝试写入”的反例样本。
- 验收标准：
  - T-50~T-54 通过（Bond SSOT 写入边界、事件化、可回放口径）；
  - T-SSOT-01 覆盖到“非 bond_dynamics 写入 Neo4j 必被阻断/被扫描命中”。
- 涉及文件路径：host_brain/bond/neo4j_client.py；host_brain/bond/bond_dynamics.py；host_brain/bond/migrations/；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-BOND-001~003；T-50~T-54；T-SSOT-01。

### TASK-0036 Host Bond Summary：派生摘要（不含敏感数值）+ context_pack 注入
- 任务描述：补齐 Bond Summary 的派生逻辑与注入口径，确保 summary 仅派生、不写回 SSOT；且不得包含敏感数值（例如 trust 原值），只允许 stage/trend/capability_hint 等桶化信息。
- 输入：DevSpec 8.2（summarizer 不得写回）、DevSpec 7（context_pack 分槽隔离）；PRD FR-BOND-003。
- 输出：
  - bond_summary 最小字段集与注入分槽（进入 context_pack.bond_summary）；
  - 禁止敏感数值的断言口径与 fixtures（含反例）；
  - 回放引用口径：summary 可指回 event_id/evidence_refs（只做引用，不宣告执行事实）。
- 验收标准：
  - T-74~T-75 通过（summary 不含敏感数值、仅派生、分槽隔离）；
  - T-CP-01 不回归（Bond Summary 不进入错误槽位）。
- 涉及文件路径：host_brain/bond/summarizer.py；host_brain/core/context_pack.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-BOND-003；T-74~T-75；T-CP-01。

### TASK-0037 配置分层收口：Host/Edge config 从占位改为可复现配置入口
- 任务描述：补齐 Host 与 Edge 的配置加载契约（env/local/default 分层、冷启动默认值、敏感项占位），替换当前 placeholder，实现可复现配置入口。
- 输入：DevSpec 1.2.1/13（环境与配置分层）；PRD FR-CORE-004、FR-OBS-001。
- 输出：
  - Host 配置对象（`Natsume_ENV`、服务开关、降级开关、超时/重试口径）；
  - Edge 配置对象（RPC 端点、能力矩阵、资源闸门默认值）；
  - 配置验证失败的稳定 reason_code/stage 映射；
  - fixtures：无 key / 缺配置 / 非法配置样例。
- 验收标准：
  - 冷启动场景输出结构稳定且可解析；
  - 同类配置缺失在同版本下 reason_code/stage 稳定；
  - contract 断言可覆盖 Host/Edge 关键配置项与默认值。
- 涉及文件路径：host_brain/app/config.py；edge_client/config.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-CORE-004；FR-OBS-001；T-Svc-01；T-67~T-71；（新增）T-Config-01。

### TASK-0038 LLM Gateway 收口：统一出站入口 + AIRLOCK 绑定 + 不可用降级
- 任务描述：补齐 `integrations/llm_gateway.py` 的统一调用契约，强制经过 AIRLOCK 出站净化与入站隔离口径，并在云端不可用时稳定降级。
- 输入：DevSpec 2.3/8.7/9/13；PRD FR-AIRLOCK-001~002、FR-CORE-002~004、FR-AUDIT-003。
- 输出：
  - gateway 请求/响应标准结构（request_id/model_profile_id/draft_only/latency）；
  - 与 airlock_log/error_log 串联的审计记录点；
  - 不可用/超时/格式漂移的 reason_code/stage 映射；
  - fixtures：provider down、timeout、格式漂移、入站隔离样例。
- 验收标准：
  - 出站未净化必拒绝并记录 AIRLOCK_BLOCKED_EGRESS；
  - 入站不可信内容不得直通执行；
  - 云端不可用时仍输出单 JSON 且 quality.stage/reason_code 可统计。
- 涉及文件路径：host_brain/integrations/llm_gateway.py；host_brain/airlock/；host_brain/core/qa.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-AIRLOCK-001~002；FR-CORE-002~004；T-39~T-40；T-67~T-71；T-LLM-01。

### TASK-0039 Metabolism 收口：Hard State 演化与 Governor 快照引用
- 任务描述：补齐 `core/metabolism.py` 的硬状态演化契约（energy/mood/sys_mode/safety），并保证 Governor 输出可引用稳定 snapshot。
- 输入：DevSpec 0/4/6/14.2；PRD FR-GOV-001、FR-CORE-001。
- 输出：
  - 硬状态快照结构与更新规则；
  - Governor 决策中的 snapshot_refs 对齐口径；
  - 异常输入的拒绝/降级 reason_code；
  - fixtures：边界状态、非法状态迁移、快照缺失样例。
- 验收标准：
  - rule_trace 能定位命中门禁与快照摘要；
  - snapshot_refs 与状态演化一致且可回放；
  - 同输入同版本下决策摘要稳定。
- 涉及文件路径：host_brain/core/metabolism.py；host_brain/governor/rules.py；host_brain/governor/decision.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-GOV-001；FR-CORE-001；T-Gov-01；T-14~T-17；（新增）T-Meta-01。

### TASK-0040 Audit Retention 收口：保留/归档/GC 策略与回放窗口保护
- 任务描述：补齐 `audit/retention.py` 的保留与 GC 契约，确保历史裁剪不破坏回放链路与 undo 窗口。
- 输入：DevSpec 8.1/8.6/13；PRD FR-AUDIT-001~003、FR-LSP-002。
- 输出：
  - retention 策略配置与执行结果结构；
  - 被清理/被阻断对象的审计记录口径；
  - 与 manifest/undo 窗口联动的保护规则；
  - fixtures：过期日志、undo 窗口内对象、阻断样例。
- 验收标准：
  - 回放链路关键键（request_id/intent_id/evidence_ref）不被破坏；
  - undo 窗口内对象不会被 GC 删除；
  - 执行与阻断均有可统计审计记录。
- 涉及文件路径：host_brain/audit/retention.py；host_brain/audit/sqlite_ledger.py；host_brain/lsp/vault.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-AUDIT-001~003；FR-LSP-002；T-Audit-01；T-Vault-02；（新增）T-Audit-RET-01。

### TASK-0041 主链收口回归：Intent→Governor→Skill→Audit 全链一致性与拒绝码稳定
- 任务描述：围绕仍处于 TASK_READY 的核心链路需求，补齐 e2e 回归与 trace 对齐，完成单向链事实闭环与拒绝码稳定性收口。
- 输入：DevSpec 3/4/5/6/8.1/9/14；PRD FR-CORE-001~004、FR-INTENT-001~003、FR-GOV-001~003、FR-TOKEN-001~003、FR-AUDIT-001。
- 输出：
  - e2e 场景：正常执行/拒绝/重放/过期/缺票据/降级；
  - 回放断言：intent_log→governor_log→skill_exec_log 串联完整；
  - reason_code 稳定性断言（同类输入同版本一致）；
  - trace 更新材料（未完成项→DONE 的证据链接）。
- 验收标准：
  - T-14~T-22、T-34~T-38、T-Audit-01、T-67~T-71 回归通过；
  - 同类拒绝码稳定可统计；
  - requirements_trace 中 R-01/R-02/R-03/R-04/R-08/R-11/R-12 至少达到 TEST_READY（目标 DONE）。
- 涉及文件路径：tests/e2e/；tests/contract/；tests/fixtures/；host_brain/governor/；host_brain/skills/；host_brain/audit/；docs/60_trace/requirements_trace.md。
- 关联需求/测试：FR-CORE-001~004；FR-INTENT-001~003；FR-GOV-001~003；FR-TOKEN-001~003；FR-AUDIT-001；T-14~T-22；T-34~T-38；T-Audit-01；T-67~T-71。

---

## 4. 增量任务清单（交付与运维收口）

### TASK-0042 启动编排收口：Host/Edge 服务启动清单 + 依赖探针
- 任务描述：把 Host/Edge 的启动入口收口为可观测编排，启动时输出统一服务状态快照并标注不可用原因。
- 输入：DevSpec 2/10/13；PRD FR-CORE-004、FR-OBS-001。
- 输出：
  - 统一启动清单结构（service_id/owner/required/status/reason_code/stage）；
  - 启动阶段健康快照输出（可序列化 JSON）；
  - 服务不可用时的稳定降级映射与审计写入点；
  - fixtures：启动时依赖缺失/端口冲突/配置缺失样例。
- 验收标准：
  - 启动阶段健康快照结构稳定且可解析；
  - 同类启动失败在同版本下 reason_code/stage 稳定；
  - 回归覆盖 `T-Svc-01` 与（新增）`T-Boot-01`。
- 涉及文件路径：host_brain/app/main.py；host_brain/core/services.py；edge_client/rpc/health.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-CORE-004；FR-OBS-001；T-Svc-01；（新增）T-Boot-01。

### TASK-0043 迁移执行器收口：SQLite/Bond Schema Version Guard + 幂等迁移
- 任务描述：落地统一迁移执行器与 schema 版本守卫，确保数据库升级/回退有确定性审计口径。
- 输入：DevSpec 8.1/8.2/14.2；PRD FR-AUDIT-001、FR-BOND-001、NFR-MAINT-001。
- 输出：
  - SQLite 与 Bond 迁移执行入口（幂等执行、版本检查）；
  - 迁移执行审计结构（migration_id/status/reason_code/latency_ms）；
  - schema 漂移检测失败的稳定拒绝码映射；
  - fixtures：重复迁移、缺迁移、版本倒退样例。
- 验收标准：
  - 重复执行迁移不产生重复副作用；
  - schema 漂移可被稳定检测并可审计；
  - 回归覆盖 `T-Audit-01`、`T-SSOT-01` 与（新增）`T-Migrate-01`。
- 涉及文件路径：host_brain/audit/migrations/；host_brain/bond/migrations/；host_brain/audit/sqlite_ledger.py；tools/static_scan/；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-AUDIT-001；FR-BOND-001；NFR-MAINT-001；T-Audit-01；T-SSOT-01；（新增）T-Migrate-01。

### TASK-0044 回放工具链收口：request_id 单键回放报告
- 任务描述：补齐回放工具链，支持基于 request_id 生成单次执行链路报告并输出缺链原因。
- 输入：DevSpec 8.1/14.6；PRD FR-AUDIT-001~003。
- 输出：
  - 回放报告结构（intent/governor/skill_exec/error/qa_violation 聚合）；
  - 缺链检测与缺失原因枚举；
  - 回放输出的稳定摘要字段（status/reason_code_chain/evidence_refs）；
  - fixtures：完整链、缺 skill_exec、缺 token_ref 样例。
- 验收标准：
  - 给定 request_id 可生成稳定结构报告；
  - 缺链时必须可解释并带稳定 reason_code；
  - 回归覆盖 `T-Audit-03` 与（新增）`T-Replay-01`。
- 涉及文件路径：tools/replay/；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-AUDIT-001~003；T-Audit-03；（新增）T-Replay-01。

### TASK-0045 交付门禁收口：contract/e2e/static-scan/doc-audience 四合一 CI
- 任务描述：建立统一交付门禁，将契约测试、e2e、静态扫描与文档受众门禁纳入同一回归入口。
- 输入：DevSpec 11/14.6；AGENTS 2.4；PRD NFR-MAINT-001。
- 输出：
  - CI 门禁分组与失败分类口径（contract/e2e/static_scan/doc_gate）；
  - 文档受众门禁与标题门禁的自动校验；
  - 失败报告结构（job_id/check_id/reason_code/failed_artifact）；
  - fixtures：文档门禁失败、静态扫描失败样例。
- 验收标准：
  - 四类门禁均可独立失败并给出可解释报告；
  - 同类门禁失败在同版本下输出结构稳定；
  - 回归覆盖（新增）`T-CI-01`。
- 涉及文件路径：.github/workflows/；tools/doc_lint/；tools/static_scan/；docs/40_agents/AGENTS.md；tests/contract/；tests/fixtures/。
- 关联需求/测试：NFR-MAINT-001；（新增）T-CI-01。

### TASK-0046 环境快照冻结：dev/test/prod 配置导出与审计追踪
- 任务描述：为 dev/test/prod 生成脱敏配置快照并写入审计引用，保证环境差异可追溯且不泄露 secrets。
- 输入：DevSpec 13.1~13.4；PRD FR-CORE-004、FR-OBS-001。
- 输出：
  - 环境快照结构（env/version/digest/override_sources/redacted_keys）；
  - 配置快照写入与读取接口；
  - 快照比对输出（added/removed/changed keys）；
  - fixtures：跨环境差异与敏感键脱敏样例。
- 验收标准：
  - 任一环境可生成脱敏快照且 digest 稳定；
  - 快照比对结果可审计并可回放；
  - 回归覆盖 `T-Config-01` 与（新增）`T-Config-02`。
- 涉及文件路径：host_brain/app/config.py；edge_client/config.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-CORE-004；FR-OBS-001；T-Config-01；（新增）T-Config-02。

### TASK-0047 发布清单收口：M0~M3 可部署资产与回滚映射
- 任务描述：沉淀 M0~M3 的发布资产清单与回滚映射，确保每次发布都有可核对的最小交付面。
- 输入：DevSpec 10/14.1；PRD FR-OBS-001、NFR-MAINT-001。
- 输出：
  - 发布清单结构（milestone/assets/checksums/required_services）；
  - 回滚映射结构（release_id/rollback_target/impact_scope）；
  - 与 Flow 指针和配置快照的交叉校验规则；
  - fixtures：缺资产、checksum 漂移、回滚目标不存在样例。
- 验收标准：
  - M0~M3 每个里程碑均可生成一致的发布清单；
  - 回滚映射可定位受影响范围并可审计；
  - 回归覆盖（新增）`T-Release-01` 与 `T-Flow-02`。
- 涉及文件路径：docs/50_project/；docs/60_trace/flows/index.md；flows/active.pointer；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-OBS-001；NFR-MAINT-001；T-Flow-02；（新增）T-Release-01。

### TASK-0048 生产演练收口：冷启动/依赖不可用/回滚三场景演练包
- 任务描述：建立三类故障演练包（冷启动、依赖不可用、回滚），验证降级输出与审计链在故障态仍稳定可回放。
- 输入：DevSpec 1.2.1/10/14.5；PRD FR-CORE-004、FR-AUDIT-001、FR-OBS-001。
- 输出：
  - 演练场景定义与输入样例；
  - 演练报告结构（scenario_id/status/reason_code_chain/replay_ref）；
  - 演练失败分类与处置口径（不含操作命令）；
  - fixtures：NoKey、provider down、回滚冲突样例。
- 验收标准：
  - 三类演练均输出可解析单 JSON 结果；
  - 故障态下 reason_code/stage 稳定且可统计；
  - 回归覆盖 `T-67~T-71` 与（新增）`T-Drill-01`。
- 涉及文件路径：tests/e2e/；tests/fixtures/；tools/replay/；host_brain/audit/；docs/60_trace/requirements_trace.md。
- 关联需求/测试：FR-CORE-004；FR-AUDIT-001；FR-OBS-001；T-67~T-71；（新增）T-Drill-01。

---

## 5. 增量任务清单（外部环境与服务可用性收口）

### TASK-0049 云节点编排收口：九节点角色/服务矩阵与探针映射
- 任务描述：沉淀 9 个云节点的统一编排矩阵（角色、承载服务、健康探针、降级策略），确保多节点运行面可审计。
- 输入：DevSpec 10/13；PRD FR-CORE-004、FR-OBS-001。
- 输出：
  - 节点清单结构（node_id/region/role/services/health_endpoint/key_profile_id）；
  - 节点-服务映射与最小冗余策略（primary/secondary）；
  - 节点健康探针输出结构（node_status/reason_code/stage/detail）；
  - fixtures：节点缺服务、角色冲突、探针超时样例。
- 验收标准：
  - 9 节点清单结构稳定且可解析；
  - 同类节点故障在同版本下 reason_code/stage 稳定；
  - 回归覆盖（新增）`T-Infra-01`。
- 涉及文件路径：configs/deploy/；host_brain/core/services.py；tests/contract/；tests/fixtures/services/；docs/60_trace/requirements_trace.md。
- 关联需求/测试：FR-CORE-004；FR-OBS-001；（新增）T-Infra-01。

### TASK-0050 密钥装配收口：多节点 LLM/API Key Profile 与引用化加载
- 任务描述：建立跨节点密钥配置 Profile 与引用化加载机制，保证“有 key 可用、无 key 降级、全程不落盘明文”。
- 输入：DevSpec 13.1~13.4；PRD FR-CORE-004、FR-OBS-001、NFR-MAINT-001。
- 输出：
  - 密钥配置结构（provider/node_id/scope/secret_ref/rotation_epoch/status）；
  - 运行时密钥解析与脱敏快照联动（仅输出 secret_ref 与 redacted 字段）；
  - 缺失 key 的稳定降级映射（cold_start/dependency）；
  - fixtures：missing key、scope 不匹配、过期 profile 样例。
- 验收标准：
  - 代码与配置仓库不出现明文 secret；
  - 无 key 场景保持 draft_only 降级并有稳定 reason_code；
  - 回归覆盖（新增）`T-Key-01` 与 `T-Config-02`。
- 涉及文件路径：host_brain/app/config.py；edge_client/config.py；configs/secrets/；tests/contract/；tests/fixtures/config/。
- 关联需求/测试：FR-CORE-004；FR-OBS-001；NFR-MAINT-001；（新增）T-Key-01；T-Config-02。

### TASK-0051 编排框架收口：LangChain/LangGraph 运行图装配与降级路径
- 任务描述：接入 LangChain/LangGraph 运行编排，形成可配置图执行入口并保持治理链（Governor/Token/Audit）不被旁路。
- 输入：DevSpec 6/8.9/10；PRD FR-CORE-001、FR-CORE-004、FR-AUDIT-001。
- 输出：
  - 图编排配置结构（graph_id/version/node_bindings/edges/fallback_edges）；
  - 编排执行结果结构（graph_status/reason_code/stage/replay_ref）；
  - 编排失败分类（节点缺失/边冲突/超时/依赖不可用）；
  - fixtures：missing node、invalid edge、graph timeout 样例。
- 验收标准：
  - 编排结果保持单 JSON 且可回放；
  - 编排失败不绕过治理链并有稳定 reason_code；
  - 回归覆盖（新增）`T-Orch-01` 与 `T-Audit-01`。
- 涉及文件路径：host_brain/integrations/；host_brain/tools/；host_brain/audit/；tests/contract/；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-CORE-001；FR-CORE-004；FR-AUDIT-001；（新增）T-Orch-01；T-Audit-01。

### TASK-0052 图数据库收口：Neo4j 外部部署配置与启动探针
- 任务描述：补齐 Neo4j 外部化连接配置、连通探针与失败映射，确保 Bond SSOT 在生产依赖下可观测可降级。
- 输入：DevSpec 8.2/10/13；PRD FR-BOND-001~003、FR-OBS-001。
- 输出：
  - Neo4j 配置结构（uri/database/auth_ref/pool/tls/enabled）；
  - 启动探针输出与失败分类（auth_failed/schema_drift/leader_unavailable）；
  - 迁移入口与探针结果的审计串联字段；
  - fixtures：鉴权失败、schema 漂移、节点不可达样例。
- 验收标准：
  - Neo4j 配置可解析且缺项可稳定拒绝；
  - 依赖不可用场景 reason_code/stage 稳定可统计；
  - 回归覆盖 `T-50~T-54`、`T-Migrate-01` 与（新增）`T-Neo4j-01`。
- 涉及文件路径：host_brain/bond/neo4j_client.py；host_brain/bond/migration_runner.py；host_brain/core/services.py；tests/contract/；tests/fixtures/bond/；tests/fixtures/services/。
- 关联需求/测试：FR-BOND-001~003；FR-OBS-001；T-50~T-54；T-Migrate-01；（新增）T-Neo4j-01。

### TASK-0053 向量库收口：ChromaDB 外部部署配置与集合治理
- 任务描述：补齐 ChromaDB 外部化配置与集合治理策略，确保 episodic/retrieval 在生产形态下可探测、可降级、可审计。
- 输入：DevSpec 8.3.1/10/13；PRD FR-MEM-003~005、FR-OBS-001。
- 输出：
  - Chroma 配置结构（endpoint/tenant/database/collection_profiles/vector_dim）；
  - 集合初始化与维度校验规则；
  - 依赖不可用时的降级输出与 reason_code 映射；
  - fixtures：collection 缺失、vector dim 不匹配、endpoint down 样例。
- 验收标准：
  - Chroma 配置与集合治理输出结构稳定；
  - 故障态可降级且审计链可回放；
  - 回归覆盖 `T-Ret-03` 与（新增）`T-Chroma-01`。
- 涉及文件路径：host_brain/memory/chroma_client.py；host_brain/retrieval/evidence_pack.py；host_brain/core/services.py；tests/contract/；tests/fixtures/retrieval/；tests/fixtures/services/。
- 关联需求/测试：FR-MEM-003~005；FR-OBS-001；T-Ret-03；（新增）T-Chroma-01。

### TASK-0054 嵌入模型收口：Embedding 服务路由与版本化配置
- 任务描述：建立嵌入模型的多提供方路由与版本化配置，保证不同节点下的 embedding 行为、降级与审计口径一致。
- 输入：DevSpec 8.3.1/13；PRD FR-MEM-003、FR-OBS-001。
- 输出：
  - 嵌入模型配置结构（provider/model_id/dim/timeout_ms/node_selector）；
  - 运行时路由与失败映射（unavailable/timeout/input_invalid）；
  - 维度漂移检测口径与审计字段；
  - fixtures：model unavailable、timeout、dim drift 样例。
- 验收标准：
  - Embedding 输出契约在多提供方下结构稳定；
  - 同类失败映射 `RETR_EMBED_*` 稳定；
  - 回归覆盖 `T-Ret-01` 与（新增）`T-Embed-01`。
- 涉及文件路径：host_brain/retrieval/embedding.py；host_brain/core/services.py；tests/contract/；tests/fixtures/retrieval/；configs/models/。
- 关联需求/测试：FR-MEM-003；FR-OBS-001；T-Ret-01；（新增）T-Embed-01。

### TASK-0055 重排模型收口：Reranker 服务路由与分数稳定化
- 任务描述：建立重排模型的服务路由与分数稳定化策略，保证候选重排在依赖异常下仍可解释并可回放。
- 输入：DevSpec 8.3.1/13；PRD FR-MEM-004、FR-OBS-001。
- 输出：
  - 重排模型配置结构（provider/model_id/top_k/timeout_ms/node_selector）；
  - 分数归一化与异常回退规则；
  - 失败分类（model_down/timeout/invalid_score_range）；
  - fixtures：model down、timeout、bad score 样例。
- 验收标准：
  - Reranker 输出契约字段稳定且可审计；
  - 同类失败映射 `RETR_RERANK_*` 稳定；
  - 回归覆盖 `T-Ret-02` 与（新增）`T-Rerank-01`。
- 涉及文件路径：host_brain/retrieval/reranker.py；host_brain/core/services.py；tests/contract/；tests/fixtures/retrieval/；configs/models/。
- 关联需求/测试：FR-MEM-004；FR-OBS-001；T-Ret-02；（新增）T-Rerank-01。

### TASK-0056 联调验收收口：外部依赖就绪报告与可用性基线
- 任务描述：形成外部依赖联调验收包，统一输出“节点/密钥/编排框架/Neo4j/Chroma/Embedding/Reranker”就绪基线与故障口径。
- 输入：DevSpec 10/11/14.5；PRD FR-CORE-004、FR-OBS-001、NFR-MAINT-001。
- 输出：
  - 联调就绪报告结构（env/node_count/key_profiles/services/graph_runtime/readiness_status）；
  - 关键依赖探针矩阵与失败分类汇总；
  - 演练摘要输出（scenario_id/status/reason_code_chain/replay_ref）；
  - fixtures：部分就绪、关键依赖不可用样例。
- 验收标准：
  - 联调报告为可解析单 JSON 且字段稳定；
  - 缺依赖时系统保持降级与审计可回放，不出现伪执行宣告；
  - 回归覆盖 `T-Svc-01`、`T-Boot-01`、`T-Drill-01` 与（新增）`T-EnvReady-01`。
- 涉及文件路径：host_brain/app/main.py；host_brain/core/services.py；tools/replay/；tests/e2e/；tests/fixtures/services/；docs/60_trace/requirements_trace.md。
- 关联需求/测试：FR-CORE-004；FR-OBS-001；NFR-MAINT-001；T-Svc-01；T-Boot-01；T-Drill-01；（新增）T-EnvReady-01。

### TASK-0057 主控闭环收口：LangGraph 读取 model_plane 并统一下发 LLM 参数
- 任务描述：将 `configs/model_plane/{model_profiles,node_registry}` 作为编排主控配置源，落实“由 LangGraph 选择节点并由 LangChain/Gateway 执行”的单入口闭环，消除模型参数散落在测试/调用方注入的状态。
- 输入：DevSpec 6/8.9/10/13；PRD FR-CORE-001、FR-CORE-004、FR-AUDIT-001。
- 输出：
  - model_plane 解析器与运行时快照结构（profile_id/node_id/provider/model_id/temperature/max_output_tokens/timeout_ms）；
  - `GraphRuntimeContract` 对 `model_profile_id` 的强约束加载路径（缺 profile、缺 node、参数非法时稳定拒绝）；
  - LLM 调用审计扩展字段（profile_id/node_id/provider/model_id/param_hash），保证可回放比对；
  - fixtures：missing profile、missing node、provider mismatch、param drift 样例。
- 验收标准：
  - 编排运行不得依赖外部临时注入 `provider_fn/model_nodes` 才可执行；
  - 模型节点参数由 model_plane 单点决定并在输出/审计中可追溯；
  - 编排失败保持单 JSON 且 reason_code 稳定，不绕过 Governor/Token/Audit；
  - 回归覆盖（新增）`T-Orch-02`、`T-Key-02` 与 `T-Audit-04`。
- 涉及文件路径：host_brain/integrations/graph_runtime.py；host_brain/integrations/llm_gateway.py；host_brain/app/config.py；host_brain/audit/sqlite_ledger.py；configs/model_plane/；tests/contract/；tests/e2e/；tests/fixtures/orchestration/。
- 关联需求/测试：FR-CORE-001；FR-CORE-004；FR-AUDIT-001；T-Orch-01；（新增）T-Orch-02；T-Key-01；（新增）T-Key-02；（新增）T-Audit-04。

### TASK-0058 参数签名收口：model_plane 参数哈希与防漂移校验
- 任务描述：对 model_plane 下发参数建立稳定 `param_hash` 与运行时校验，避免 provider/model_id/温度/token_cap 在执行链路发生不可审计漂移。
- 输入：DevSpec 10/13；PRD FR-CORE-004、FR-AUDIT-001。
- 输出：
  - 参数签名规则（provider/model_id/temperature/max_output_tokens/timeout_ms 的 canonical 序列化与 hash）；
  - 编排执行期参数防漂移校验（期望签名 vs 实际签名）；
  - 审计字段扩展（expected_param_hash/actual_param_hash/drift_detected）；
  - fixtures：hash mismatch、missing hash、tampered params 样例。
- 验收标准：
  - 任意模型调用均可输出稳定 `param_hash` 并可回放；
  - 发现漂移时稳定拒绝或降级且 reason_code 可统计；
  - 回归覆盖（新增）`T-Orch-03` 与 `T-Audit-05`。
- 涉及文件路径：host_brain/integrations/graph_runtime.py；host_brain/integrations/llm_gateway.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/orchestration/。
- 关联需求/测试：FR-CORE-004；FR-AUDIT-001；（新增）T-Orch-03；（新增）T-Audit-05。

### TASK-0059 路由治理收口：profile→node 选择器与环境约束矩阵
- 任务描述：实现 model_profile 到 node 的确定性选择器，并把 env/node_selector/availability 约束纳入主流程路由，确保同输入同环境得到同路由结果。
- 输入：DevSpec 6/13；PRD FR-CORE-001、FR-OBS-001。
- 输出：
  - profile→node 选择器契约（输入：profile/env/task；输出：selected_node/reason_code）；
  - 环境约束矩阵（dev/staging/prod 与 node_selector/availability 的解析口径）；
  - 审计字段（selected_node/candidate_nodes/reject_reason）；
  - fixtures：no candidate、multi candidate tie-break、env mismatch 样例。
- 验收标准：
  - 选择器结果确定性且可解释；
  - 无候选时稳定降级并保持单 JSON 输出；
  - 回归覆盖（新增）`T-Orch-04` 与 `T-EnvReady-02`。
- 涉及文件路径：host_brain/integrations/graph_runtime.py；host_brain/core/services.py；host_brain/app/main.py；tests/contract/；tests/e2e/；tests/fixtures/orchestration/；tests/fixtures/services/。
- 关联需求/测试：FR-CORE-001；FR-OBS-001；（新增）T-Orch-04；（新增）T-EnvReady-02。

### TASK-0060 票据联动收口：模型调用与 Governor/Token 审批锚点串联
- 任务描述：将模型调用（尤其可能触发高风险意图草案的节点）与 Governor/Token 审批锚点串联，形成“模型建议→治理决策→执行入口”的可回放闭环。
- 输入：DevSpec 8.9/10；PRD FR-TOOL-003、FR-AUDIT-001、FR-CORE-001。
- 输出：
  - 模型调用到治理链路的串联键（request_id/intent_id/approval_token_ref/evidence_ref）；
  - 审批前后阶段字段（precheck_stage/governor_stage/execution_stage）；
  - 拒绝码映射（missing_token/invalid_token/governor_reject）；
  - fixtures：model suggests write/no token、token expired、governor reject 样例。
- 验收标准：
  - 高风险路径不得因模型节点成功而绕过 Governor/Token；
  - 外部“已执行”叙事必须可指向 evidence_ref；
  - 回归覆盖（新增）`T-Orch-05` 与 `T-Tool-04`。
- 涉及文件路径：host_brain/integrations/graph_runtime.py；host_brain/governor/decision.py；host_brain/governor/tokens.py；host_brain/tools/broker.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-CORE-001；FR-TOOL-003；FR-AUDIT-001；（新增）T-Orch-05；（新增）T-Tool-04；I1/I8/I9。

### TASK-0061 审计归档收口：model_control 入库与 request 级回放透出
- 任务描述：把编排输出中的 `model_control` 与治理锚点写入审计存储并接入 request 级回放，形成“参数签名—票据—执行状态”的可检索闭环。
- 输入：DevSpec 10/11；PRD FR-AUDIT-001、FR-CORE-001。
- 输出：
  - 审计表字段扩展（request_id/node_id/model_profile_id/provider/model_id/expected_param_hash/actual_param_hash/drift_detected/approval_token_ref/evidence_ref）；
  - 回放工具输出扩展（按 request_id 聚合 model_control 与治理锚点）；
  - fixtures：audit row 缺关键字段、回放缺链路、hash 漂移入库样例。
- 验收标准：
  - 任意一次模型调用可通过 request_id 回放到参数签名与票据锚点；
  - 漂移与票据缺失在回放报告中具备稳定 reason_code 与阶段字段；
  - 回归覆盖（新增）`T-Audit-06`、`T-Replay-02`。
- 涉及文件路径：host_brain/audit/sqlite_ledger.py；tools/replay/；host_brain/integrations/graph_runtime.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-AUDIT-001；FR-CORE-001；（新增）T-Audit-06；（新增）T-Replay-02。

### TASK-0062 配置治理收口：model_plane 版本签名与发布门禁联动
- 任务描述：将 model_plane 资产纳入发布门禁，固化“配置版本→参数签名→生效环境”的一致性校验，避免灰度期间配置漂移不可控。
- 输入：DevSpec 1.5/14.5；PRD FR-FLOW-001、FR-OBS-001、NFR-MAINT-001。
- 输出：
  - model_plane 版本清单（profile_version/node_registry_version/asset_digest）；
  - 发布前一致性校验（active pointer 与 model_plane digest 对齐）；
  - 回滚映射（版本回退时签名一致性检查）；
  - fixtures：digest mismatch、stale pointer、rollback digest 缺失样例。
- 验收标准：
  - 发布门禁可阻断 model_plane 与 active 配置不一致；
  - 回滚路径可验证签名一致且可审计；
  - 回归覆盖（新增）`T-Flow-03`、`T-Release-01`。
- 涉及文件路径：flows/active.pointer；configs/model_plane/；host_brain/release/；tests/contract/；tests/fixtures/；docs/60_trace/flows/index.md。
- 关联需求/测试：FR-FLOW-001；FR-OBS-001；NFR-MAINT-001；（新增）T-Flow-03；（新增）T-Release-01。

### TASK-0063 联调验收补齐：编排主控全链路演练与故障注入矩阵
- 任务描述：补齐“编排主控 + model_plane + Governor/Token + Audit”联调演练矩阵，形成可复现实战故障包与验收报告模板。
- 输入：DevSpec 10/11/14.5；PRD FR-CORE-004、FR-AUDIT-001、FR-TOOL-003。
- 输出：
  - 演练场景矩阵（profile 缺失、selector 无候选、param drift、approval token missing、provider down）；
  - 标准化报告结构（scenario_id/stage/status/reason_code_chain/replay_ref/model_profile_id）；
  - 故障注入 fixtures 与期望断言文档；
  - 环境就绪报告补充字段（orchestration_control_coverage）。
- 验收标准：
  - 五类核心故障场景均可稳定复现并输出单 JSON 报告；
  - 每个场景可通过回放工具定位到 request_id 与关键审计字段；
  - 回归覆盖（新增）`T-Drill-02`、`T-EnvReady-03`、`T-Orch-06`。
- 涉及文件路径：tests/e2e/；tests/fixtures/drill/；host_brain/app/main.py；tools/replay/；host_brain/integrations/graph_runtime.py；docs/60_trace/requirements_trace.md。
- 关联需求/测试：FR-CORE-004；FR-AUDIT-001；FR-TOOL-003；（新增）T-Drill-02；（新增）T-EnvReady-03；（新增）T-Orch-06。

### TASK-0064 运行链路可视化补齐（只读观测 API + request 时间线聚合）
- 任务描述：补齐“无可视化界面难以监控链路细节”的缺口，先落地只读观测接口，输出 request 级链路时间线与来源汇总，作为后续 UI 面板的数据底座；并将 Agent 监测方向切到 LangSmith，同时在系统启动时输出详细数据流日志并支持本地监控控制台窗口（best-effort）。
- 输入：DevSpec 10/11；现有审计链（intent/governor/skill_exec/channel/avatar/clawd/voice/retrieval/vision/service_health）。
- 输出：
  - request 级观测报告契约（request_id/trace_status/trace_stage/trace_reason_code/timeline_total/sources/timeline）；
  - 时间线聚合规则（按 ts_ms 与 source 稳定排序）；
  - Agent 监测配置契约（LangSmith provider/status/reason_code/project/endpoint）；
  - 启动期数据流日志打印（startup/readiness/monitoring profile/console profile）；
  - 本地监控控制台窗口（spawn best-effort，默认 inline）；
  - 只读约束：不新增执行入口、不改 Governor/Token/Audit 决策链；
  - 回归测试（新增）`T-Obs-01`、`T-Obs-02`。
- 验收标准：
  - 任意 request_id 可输出可解析单 JSON 时间线报告；
  - sources 统计与 timeline 条目一致且稳定可回放；
  - 空 request_id 被稳定拒绝（输入契约错误）；
  - LangSmith tracing 关闭/缺 key 时输出稳定 status+reason_code；
  - 监控控制台在不支持 spawn 的平台稳定降级为 inline 模式；
  - 回归覆盖（新增）`T-Obs-01`、`T-Obs-02`。
- 涉及文件路径：host_brain/app/main.py；tests/contract/test_observability_timeline_contract.py；tests/contract/test_runtime_monitoring_contract.py；docs/30_devspec/DevTasks.md。
- 关联需求/测试：FR-OBS-001；FR-AUDIT-001；（新增）T-Obs-01；（新增）T-Obs-02；I1/I8/I9。

---

## 6. 增量任务清单（审阅报告缺口补齐：服务化/可视化/设备集成/工程改善）

> 来源：full_code_review_report_2026-03-02.md 第五节"关键缺口与改进建议"。
> 按优先级排列：P0（高）→ P1（中）→ P2（低）。

### TASK-0065 HTTP 服务框架落地：FastAPI 路由绑定 + 健康检查 + 链路入口（P0）
- 任务描述：将现有函数聚合层（`main.py` 中的 `build_service_health_report`、`build_startup_report`、`build_env_readiness_report` 等）绑定到 FastAPI 路由，形成可直接 `python -m host_brain` 启动的 HTTP 服务，对外暴露 `POST /v1/chat`（链路入口）与 `GET /health`（健康检查），并保证 Governor/Token/Audit 治理链不被旁路。
- 输入：DevSpec 2.1（Host 进程："对外提供 `POST /v1/chat`"）；DevSpec 1.1（`host_brain/app/main.py` 定义为"HTTP API 入口"）；PRD 2.1（"编排入口服务 Host Orchestrator API：对外 chat 入口 + 事件汇聚"）。
- 输出：
  - FastAPI Application 实例与路由注册（`/v1/chat`、`/health`、`/readiness`、`/metrics`）；
  - 请求→编排→输出的标准 JSON 响应结构（复用已有 `quality.stage`/`reason_code` 口径）；
  - 启动生命周期钩子（startup/shutdown）串联服务探针与审计写入；
  - 中间件层：request_id 注入、CORS（可配置）、异常兜底（保持单 JSON 输出）；
  - `pyproject.toml` / `requirements.txt` 新增 `fastapi`、`uvicorn` 依赖并锁定版本；
  - fixtures：正常请求、缺字段、服务不可用场景样例。
- 验收标准：
  - `python -m host_brain` 可启动 HTTP 服务并响应 `/health`；
  - `POST /v1/chat` 输入后经 Governor 裁决并返回单 JSON 响应（含 narrative/intent_draft/quality）；
  - 异常/降级场景仍输出可解析单 JSON 且 reason_code 稳定；
  - 不引入旁路执行路径（所有执行仍经 Governor/Token/Skills）；
  - 回归覆盖（新增）`T-HTTP-01`（路由可达性与响应结构）、`T-HTTP-02`（异常兜底与降级输出）。
- 涉及文件路径：host_brain/app/main.py；host_brain/app/config.py；host_brain/pyproject.toml（或 requirements.txt）；tests/contract/；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-CORE-001；FR-CORE-002；FR-CORE-004；FR-OBS-001；（新增）T-HTTP-01；T-HTTP-02。

### TASK-0066 链路可视化面板：审计数据 Web UI 呈现与 request 级排障（P0）
- 任务描述：基于 TASK-0064 落地的只读观测 API，构建最小可用的 Web UI 面板，支持按 request_id/session_id 检索链路时间线、查看 Governor 裁决详情、审计事件聚合，降低排障对人工查表的依赖；UI 为只读，不新增任何执行入口。
- 输入：DevSpec 10/11（可观测性）；TASK-0064 输出的 request 级观测报告契约；PRD FR-OBS-001（"关键指标与面板"）。
- 输出：
  - 前端页面（可选 React/Vue/纯 HTML+JS，以最小可运行为准）；
  - 页面功能：request_id 搜索、时间线可视化、source 过滤、reason_code 统计图表；
  - 与 Host API 的集成（`GET /v1/observe/{request_id}` 或等价端点）；
  - 静态资源打包与部署口径（可嵌入 FastAPI static files 或独立部署）；
  - fixtures：示例观测报告数据。
- 验收标准：
  - 输入 request_id 后可在浏览器看到链路时间线与 source 分布；
  - 空结果/错误 request_id 有友好提示且不崩溃；
  - 面板不引入任何执行入口（纯只读）；
  - 回归覆盖（新增）`T-UI-01`（面板可渲染与 API 联通）。
- 涉及文件路径：host_brain/app/main.py；host_brain/app/static/（或 frontend/）；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-OBS-001；FR-OBS-002；（新增）T-UI-01。

### TASK-0067 Edge ASR 设备集成：faster-whisper 实际 SDK 对接与事件桥接（P0）
- 任务描述：将 Edge 端 ASR 从契约层定义推进到实际 faster-whisper（large-v3）SDK 对接，形成"麦克风输入→ faster-whisper 转写→ asr_chunk 事件上送"的完整链路，并保持与已有 RPC 契约（TASK-0024/TASK-0025）的结构兼容。
- 输入：DevSpec 8.11.3（VOICE 契约：asr_chunk 字段）；TASK-0024/TASK-0025 输出的 RPC 端点与事件 Schema；PRD FR-VOICE-001。
- 输出：
  - faster-whisper 模型加载与推理封装（GPU 常驻）；
  - 音频流采集与分片策略（VAD 或固定窗口）；
  - 转写结果→ asr_chunk 事件映射（text/timestamps/confidence/alternates）；
  - 资源闸门（TASK-0029）集成：ASR 优先级最高；
  - 模型不可用/GPU OOM 的稳定降级与 reason_code 映射；
  - `requirements.txt` 新增 `faster-whisper`、`sounddevice`（或等价）依赖；
  - fixtures：音频样本（短/长/静音/低信噪比）。
- 验收标准：
  - 实际麦克风输入可产出结构稳定的 asr_chunk 事件；
  - 低置信度显式标注；GPU 不可用时快速失败并有稳定 reason_code；
  - 与已有 T-Voice-01 契约兼容；
  - 回归覆盖（新增）`T-ASR-HW-01`（实际设备链路可达性）。
- 涉及文件路径：edge_client/senses/asr.py；edge_client/rpc/voice.py；edge_client/rpc/resource_gate.py；edge_client/requirements.txt；tests/contract/；tests/fixtures/voice/。
- 关联需求/测试：FR-VOICE-001；T-Voice-01；（新增）T-ASR-HW-01。

### TASK-0068 Edge TTS/VTS 设备集成：GPT-SoVITS 合成 + VTube Studio SDK 对接（P0）
- 任务描述：将 Edge 端 TTS 与 VTS 从契约层定义推进到实际 SDK 对接，形成"Host 下发文本→ GPT-SoVITS 合成→播放队列→ VTube Studio 口型/表情驱动"的完整链路，保持与已有 RPC 契约（TASK-0026/TASK-0021）的结构兼容。
- 输入：DevSpec 8.11.3（VOICE 契约）、DevSpec 8.11.2（VTS 外显）；TASK-0026/TASK-0021 输出的 RPC 端点与事件 Schema；PRD FR-VOICE-002、FR-AVATAR-001。
- 输出：
  - GPT-SoVITS 模型加载与推理封装（GPU 常驻，全量不量化）；
  - 合成结果→播放队列→音频设备输出链路；
  - VTube Studio API 连接封装：talk gate（TALK_START/TALK_END）与 emotion_tag→expression_id 映射驱动；
  - barge-in 打断时的合成中止与 VTS 恢复策略；
  - 模型不可用/VTS 连接断开的稳定降级与 reason_code 映射；
  - `requirements.txt` 新增 `pyvts`（或等价 VTS SDK）依赖；
  - fixtures：合成失败/VTS 断连/barge-in 场景样例。
- 验收标准：
  - Host 下发文本可在 Edge 完成合成并播放音频；
  - VTS 口型与表情随播放联动，停止时恢复默认表情；
  - 合成失败/VTS 断连场景有稳定 reason_code 且审计可回放；
  - 与已有 T-Voice-02、T-Avatar-01 契约兼容；
  - 回归覆盖（新增）`T-TTS-HW-01`（TTS 实际合成链路）、`T-VTS-HW-01`（VTS 实际驱动链路）。
- 涉及文件路径：edge_client/actuators/speech.py；edge_client/rpc/voice.py；host_brain/channels/vts.py；edge_client/requirements.txt；tests/contract/；tests/fixtures/voice/。
- 关联需求/测试：FR-VOICE-002；FR-AVATAR-001；T-Voice-02；T-Avatar-01；（新增）T-TTS-HW-01；T-VTS-HW-01。

### TASK-0069 graph_runtime 拆分：契约层/执行层/降级层分离（P1）
- 任务描述：将 `integrations/graph_runtime.py`（当前 942 行单文件）按职责拆分为契约层（配置校验/Schema）、执行层（节点执行/边路由）、降级层（fallback/timeout/dependency 处理），降低单文件复杂度并提升可测试性，拆分后行为与输出结构必须与拆分前保持一致。
- 输入：DevSpec 6/8.9/10；TASK-0051 输出的图编排契约；现有 `graph_runtime.py` 实现。
- 输出：
  - `integrations/graph_contract.py`：图配置 Schema 校验、节点/边合法性验证；
  - `integrations/graph_executor.py`：节点执行、依赖检查、边路由；
  - `integrations/graph_fallback.py`：超时/依赖不可用/节点缺失的统一降级处理；
  - `integrations/graph_runtime.py`：缩减为编排入口（串联三层），保持对外接口不变；
  - 拆分前后输出结构（graph_status/reason_code/stage/fallback_map/dependency_statuses）完全一致；
  - fixtures：拆分前后回归对比数据。
- 验收标准：
  - 拆分后 T-Orch-01 及所有现有图编排相关测试无回归；
  - 单文件行数控制在 300 行以内；
  - 各层可独立单测且依赖注入清晰；
  - 回归覆盖（新增）`T-Orch-Refactor-01`（拆分前后输出一致性）。
- 涉及文件路径：host_brain/integrations/graph_runtime.py；host_brain/integrations/graph_contract.py（新增）；host_brain/integrations/graph_executor.py（新增）；host_brain/integrations/graph_fallback.py（新增）；tests/contract/；tests/fixtures/orchestration/。
- 关联需求/测试：FR-CORE-001；FR-CORE-004；T-Orch-01；（新增）T-Orch-Refactor-01。

### TASK-0070 SQLite Ledger 并发优化：WAL 模式启用与写入吞吐基准（P1）
- 任务描述：为 SQLite Ledger 启用 WAL（Write-Ahead Logging）模式，提升高并发场景下的读写吞吐，并建立写入性能基准测试，为后续是否需要迁移到外部审计存储提供数据依据。
- 输入：DevSpec 8.1（Ledger SSOT）；TASK-0011 输出的 Triptych 三联画表；审阅报告 P1-5 建议。
- 输出：
  - SQLite 连接初始化时启用 `PRAGMA journal_mode=WAL` 与推荐配置（`synchronous=NORMAL`、`wal_autocheckpoint`）；
  - 并发读写保护策略（连接池/写锁/重试口径）；
  - 写入性能基准测试脚本（单线程/多线程/混合读写场景）；
  - 基准报告结构（scenario/ops_per_sec/p95_latency_ms/wal_size）；
  - WAL 模式启用失败的降级策略（回退 DELETE 模式并记录 reason_code）；
  - fixtures：高并发写入样例。
- 验收标准：
  - WAL 模式启用后现有审计链测试无回归；
  - 性能基准可量化并输出稳定报告结构；
  - WAL 启用失败不阻塞系统启动（降级并可审计）；
  - 回归覆盖 T-Audit-01 与（新增）`T-Audit-WAL-01`（WAL 模式下审计链完整性）。
- 涉及文件路径：host_brain/audit/sqlite_ledger.py；host_brain/app/config.py；tools/bench/（新增）；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-AUDIT-001；T-Audit-01；（新增）T-Audit-WAL-01。

### TASK-0071 E2E 补充：微信入站→Governor→VTS/语音→审计 一体化场景（P1）
- 任务描述：补充"微信消息入站→Host 编排→Governor 裁决→VTS 表情驱动 + 语音播报→审计链写入"的端到端测试场景，覆盖正常执行、拒绝、降级三种路径，验证跨模块串联与输出稳定性。
- 输入：DevSpec 8.11.1/8.11.2/8.11.3；TASK-0020（微信通道）、TASK-0021（VTS）、TASK-0024~0027（语音链路）、TASK-0041（主链回归）。
- 输出：
  - E2E 场景定义：正常（微信→chat→VTS表情+语音播报→审计）、拒绝（Governor 拒绝→无执行→审计）、降级（VTS 不可用→仅文本→审计）；
  - 每个场景的输入 fixtures 与期望输出断言；
  - 审计串联验证：request_id 可从 channel_io_log→governor_log→avatar_action_log/voice_event_log 串联；
  - 跨端模拟策略（Edge mock 或 stub）。
- 验收标准：
  - 三种路径 E2E 测试通过；
  - 审计链串联键（request_id/thread_key）完整可回放；
  - VTS/语音不可用时输出结构稳定且 reason_code 可解释；
  - 回归覆盖（新增）`T-E2E-WeChat-01`（微信→Governor→VTS/语音全链路）。
- 涉及文件路径：tests/e2e/test_wechat_vts_voice_e2e.py（新增）；tests/fixtures/channel/；tests/fixtures/voice/；host_brain/channels/；host_brain/governor/；host_brain/audit/。
- 关联需求/测试：FR-CHANNEL-001；FR-AVATAR-001；FR-VOICE-001~003；T-Channel-01；T-Avatar-01；T-Voice-01~03；（新增）T-E2E-WeChat-01。

### TASK-0072 跨端事件时间线：Host/Edge 统一 request_id/session_id 关联视图（P1）
- 任务描述：建立 Host 与 Edge 事件的统一时间线聚合机制，支持按 request_id 或 session_id 关联两端事件（Host 审计 + Edge RPC/资源/语音事件），输出可排序时间线，为排障与性能分析提供数据底座。
- 输入：DevSpec 10/11；TASK-0064 输出的 Host 端观测报告；TASK-0024~0029 输出的 Edge 端事件结构。
- 输出：
  - Edge 端事件上报结构（event_type/ts_ms/request_id/session_id/payload）；
  - Host 端事件聚合器：合并 Host 审计事件 + Edge 上报事件，按 ts_ms 排序；
  - 统一时间线输出结构（timeline[]，含 source=host/edge 标记）；
  - 时间漂移容忍策略（Edge/Host 时钟不完全同步时的排序口径）；
  - fixtures：Host/Edge 混合事件、时间交叉、Edge 事件缺失样例。
- 验收标准：
  - 给定 request_id 可输出 Host+Edge 合并时间线；
  - Edge 事件缺失时 Host 时间线仍完整且标注缺失来源；
  - 时间线排序稳定且可回放；
  - 回归覆盖（新增）`T-Obs-03`（跨端时间线聚合与排序稳定性）。
- 涉及文件路径：host_brain/app/main.py；edge_client/rpc/；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/e2e/；tests/fixtures/。
- 关联需求/测试：FR-OBS-001；FR-AUDIT-001；T-Obs-01；（新增）T-Obs-03。

### TASK-0073 Rate Limit 持久化：R4 滑动窗口外部存储适配（P2）
- 任务描述：将 Governor R4 Rate Limit Gate 的滑动窗口计数器从当前内存实现改为可插拔存储适配（内存/Redis/SQLite），支持集群模式下的共享限流，并保持单机模式零外部依赖。
- 输入：DevSpec 6（R4 Rate Limit Gate）；TASK-0008 输出的 R4 Gate 契约。
- 输出：
  - Rate Limit 存储接口（抽象层：`incr`/`get_count`/`reset`/`ttl`）；
  - 三种实现：InMemory（默认）、SQLite（单机持久化）、Redis（集群模式）；
  - 配置项：`RATE_LIMIT_BACKEND=memory|sqlite|redis`；
  - Redis 不可用时降级到 InMemory 并记录 reason_code；
  - fixtures：窗口滑动/过期/并发竞争样例。
- 验收标准：
  - 切换 backend 后 R4 Gate 行为一致（同输入同结果）；
  - Redis 不可用时稳定降级且 rule_trace 可标注 backend 信息；
  - 现有 T-Gov-01 无回归；
  - 回归覆盖（新增）`T-Gov-R4-01`（Rate Limit 多 backend 一致性）。
- 涉及文件路径：host_brain/governor/rules.py；host_brain/governor/rate_limit_store.py（新增）；host_brain/app/config.py；tests/contract/；tests/fixtures/。
- 关联需求/测试：FR-GOV-001；T-Gov-01；（新增）T-Gov-R4-01。

### TASK-0074 Camera Events 扩展：多检测器注册 + 多模态事件聚合（P2）
- 任务描述：将当前基础摄像头事件识别扩展为多检测器注册与多模态事件聚合（人脸/物体/手势/场景），每个检测器独立输出置信度与 evidence_ref，聚合后输出统一事件结构。
- 输入：DevSpec 8.12；TASK-0034 输出的 camera_event 结构；PRD FR-VISION-004。
- 输出：
  - 检测器注册表结构（detector_id/type/model_id/confidence_threshold/enabled）；
  - 多检测器并行执行与结果聚合策略；
  - 聚合事件结构（detections[]{detector_id/type/label/confidence/bbox?/evidence_ref}）；
  - 检测器不可用时的部分降级策略（可用检测器正常输出，不可用检测器标注 reason_code）；
  - fixtures：多检测器混合、部分不可用、全不可用样例。
- 验收标准：
  - 多检测器并行输出结构稳定且各自 evidence_ref 独立；
  - 部分检测器不可用时输出仍合法且降级可解释；
  - 现有 T-Vision-02 无回归；
  - 回归覆盖（新增）`T-Vision-03`（多检测器注册与聚合稳定性）。
- 涉及文件路径：host_brain/vision/camera_events.py；host_brain/vision/detector_registry.py（新增）；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/vision/。
- 关联需求/测试：FR-VISION-004；T-Vision-02；（新增）T-Vision-03。

### TASK-0075 Airlock DRAFT_CODE 沙箱执行：隔离运行环境 + 结果审计（P2）
- 任务描述：将 Airlock 入站隔离中 DRAFT_CODE 分类从"仅标记"推进到实际沙箱执行能力（隔离进程/资源限制/超时保护），执行结果作为草案证据写入审计链，不直接影响主链路状态。
- 输入：DevSpec 8.7（Airlock 入站隔离）；TASK-0016 输出的 quarantine 分类契约；PRD FR-AIRLOCK-002。
- 输出：
  - 沙箱执行环境封装（subprocess/容器/限时限资源）；
  - 执行结果结构（sandbox_id/status/stdout/stderr/exit_code/duration_ms/resource_usage）；
  - 沙箱结果→审计写入（sandbox_exec_log）与 evidence_ref 串联；
  - 安全约束：无网络/无文件系统写入（或仅临时目录）/资源上限/超时强杀；
  - 沙箱不可用时稳定拒绝（SANDBOX_UNAVAILABLE）；
  - fixtures：正常执行/超时/恶意代码/资源超限样例。
- 验收标准：
  - DRAFT_CODE 可在沙箱中执行并产出结构化结果；
  - 超时/资源超限场景强制终止且 reason_code 稳定；
  - 沙箱执行不影响主进程状态且不引入旁路执行路径；
  - 现有 T-39/T-40 无回归；
  - 回归覆盖（新增）`T-Sandbox-01`（沙箱执行与安全边界）。
- 涉及文件路径：host_brain/airlock/sandbox.py（新增）；host_brain/airlock/quarantine.py；host_brain/audit/sqlite_ledger.py；tests/contract/；tests/fixtures/airlock/。
- 关联需求/测试：FR-AIRLOCK-002；T-39；T-40；（新增）T-Sandbox-01。

---

## 阶段 8：质量加固 + 风险消除 + Edge 集成深化

> 说明：本阶段任务来源于代码审阅报告（full_code_review_report_2026-03-02.md）、风险注册表（risk_registry.md）、以及 P1/P2 后续行动汇总。按优先级排列：先修复代码质量微调（可立即执行），再推进安全升级与 Edge 集成深化。

### TASK-0076 代码质量微调：裸 print / 类型标注 / SQL 安全 / 表结构缓存 / 可选依赖文档（P2）
- 任务描述：集中处理代码审阅报告中的 P2 发现项（F-13~F-16, F-12），确保代码质量零遗留。
- 输入：代码审阅报告 §七 P2 发现项（F-13/F-14/F-15/F-16/F-12）。
- 输出：
  - `main.py:908` 裸 `print()` → `logger.info()`（F-13）；
  - `graph_executor.py:43` 补充返回类型标注 `-> None`（F-14）；
  - `fastapi_app.py:582,605` 两个 async handler 补充返回类型标注（F-15）；
  - `sqlite_ledger.py._fetch_all()` 增加表名白名单校验（硬编码允许表名集合，拒绝非法表名并记录 `AUDIT_TABLE_NAME_INVALID`）（F-16）；
  - `sqlite_ledger.py._table_has_column()` 增加表结构缓存（`_column_cache: dict[str, set[str]]`），避免每次调用 PRAGMA（F-06）；
  - `requirements.txt` 增加注释标注 `redis` 为可选依赖（F-12）。
- 验收标准：
  - 零裸 `print()` 残留；
  - 所有公开函数/方法有返回类型标注；
  - `_fetch_all` 传入非法表名时抛出 ValueError 或返回空并记录 reason_code；
  - `_table_has_column` 首次调用后缓存命中率 = 100%（同表重复调用不触发 PRAGMA）；
  - 现有 494 项测试无回归；
  - 回归覆盖（新增）`T-Quality-01`（代码质量加固断言）。
- 涉及文件路径：host_brain/app/main.py；host_brain/integrations/graph_executor.py；host_brain/app/fastapi_app.py；host_brain/audit/sqlite_ledger.py；requirements.txt；tests/contract/test_quality_contract.py（新增）。
- 关联需求/测试：NFR-MAINT-001；FR-AUDIT-001；（新增）T-Quality-01。

### TASK-0077 沙箱安全升级：subprocess → Docker/nsjail 容器级隔离（P1 安全）
- 任务描述：将 DRAFT_CODE 沙箱从当前 subprocess + 临时目录隔离，升级为容器级隔离（优先 Docker，备选 nsjail），确保恶意代码无法读取/写入主机文件系统、无法访问网络。
- 输入：risk_registry.md R41-1/R41-2；DevSpec 8.7；TASK-0075 输出的 `sandbox.py`。
- 输出：
  - `SandboxConfig` 新增 `isolation_backend` 字段（`subprocess`/`docker`/`nsjail`），默认 `subprocess`（向后兼容）；
  - Docker 后端实现：自动构建最小沙箱镜像 + 只读挂载代码 + 无网络 + 资源限制（CPU/内存/超时） + 输出采集；
  - nsjail 后端实现（可选/备选）：seccomp + 用户 namespace + 只读根文件系统；
  - 容器后端不可用时自动降级到 subprocess 后端 + `SANDBOX_CONTAINER_UNAVAILABLE` reason_code；
  - 跨平台验证（Windows：仅 subprocess；Linux：Docker 优先）；
  - fixtures：Docker 隔离成功/Docker 不可用降级/恶意文件读取被拒。
- 验收标准：
  - Docker 后端下恶意代码无法读取主机文件系统（`/etc/passwd` 等）；
  - Docker 不可用时稳定降级到 subprocess 后端且 reason_code 可统计；
  - 现有 T-Sandbox-01 无回归；
  - 回归覆盖（新增）`T-Sandbox-02`（容器级隔离安全边界）。
- 涉及文件路径：host_brain/airlock/sandbox.py；host_brain/airlock/docker_sandbox.py（新增）；host_brain/core/errors.py；tests/contract/test_sandbox_docker_contract.py（新增）；tests/fixtures/airlock/。
- 关联需求/测试：FR-AIRLOCK-002；T-Sandbox-01；（新增）T-Sandbox-02。

### TASK-0078 跨端时间线 SQL 优化：按时间窗口过滤 + 分页查询（P1 性能）
- 任务描述：解决 `cross_timeline.py` 和 `web_ui.py` 全量内存加载审计表的性能隐患，为 SQLiteLedger 增加按时间窗口过滤的查询方法，并在时间线聚合与面板 API 中使用。
- 输入：risk_registry.md R38-3/R32-2；代码审阅报告 F-03。
- 输出：
  - `sqlite_ledger.py` 新增 `fetch_by_time_range(table, start_ms, end_ms, limit, offset)` 方法（参数化查询，表名白名单校验）；
  - `cross_timeline.py` 的 Host 审计表遍历改为按时间窗口查询；
  - `web_ui.py` 的 API 端点支持 `?start_ms=&end_ms=&limit=&offset=` 分页参数；
  - fixtures：大量数据分页/空窗口/窗口越界样例。
- 验收标准：
  - 10000 条审计记录下时间线聚合耗时 < 100ms（基准测试口径）；
  - 面板 API 支持分页且默认 limit=100；
  - 现有 T-Obs-03 / T-UI-01 无回归；
  - 回归覆盖（新增）`T-Obs-04`（时间窗口查询与分页）。
- 涉及文件路径：host_brain/audit/sqlite_ledger.py；host_brain/observability/cross_timeline.py；host_brain/app/web_ui.py；tests/contract/test_timeline_perf_contract.py（新增）；tests/fixtures/observability/。
- 关联需求/测试：FR-OBS-001；FR-AUDIT-001；T-Obs-03；T-UI-01；（新增）T-Obs-04。

### TASK-0079 Edge→Host RPC 事件批量上报接口（P1 跨端集成）
- 任务描述：实现 Edge→Host 的事件批量上报 RPC 通道，将当前模拟 Edge 事件的测试方式升级为可对接真实 RPC 的实现。
- 输入：risk_registry.md R38-1/R37-1；TASK-0072 输出的 `EdgeEventReport` 结构；DevSpec 7.3。
- 输出：
  - `edge_client/rpc/event_reporter.py`（新增）：批量事件上报客户端，支持重试 + 批次控制 + 网络异常降级；
  - `host_brain/app/fastapi_app.py` 新增 `POST /v1/edge/events` 端点：接收 `EdgeEventReport[]`，写入审计并返回确认；
  - 事件上报协议结构（JSON Schema：event_type/source/ts_ms/payload/evidence_ref）；
  - 网络不可用时 Edge 端缓存事件并在恢复后批量补发；
  - fixtures：正常批量上报/部分失败/网络中断缓存。
- 验收标准：
  - Edge 事件可通过 HTTP 批量上报到 Host 并写入审计链；
  - 网络中断时 Edge 端缓存事件（最多 1000 条或 10MB），恢复后自动补发；
  - 上报事件与 `cross_timeline.py` 无缝对接；
  - 现有 T-Obs-03 / T-E2E-WeChat-01 无回归；
  - 回归覆盖（新增）`T-RPC-Event-01`（Edge→Host 事件上报协议）。
- 涉及文件路径：edge_client/rpc/event_reporter.py（新增）；host_brain/app/fastapi_app.py；host_brain/observability/cross_timeline.py；tests/contract/test_rpc_event_contract.py（新增）；tests/fixtures/e2e/。
- 关联需求/测试：FR-VOICE-001；FR-AUDIT-001；T-Obs-03；T-E2E-WeChat-01；（新增）T-RPC-Event-01。

### TASK-0080 VTS WebSocket 自动重连机制（P1 Edge 网络）
- 任务描述：为 VTubeStudioDriver 添加 WebSocket 自动重连策略（指数退避 + 最大重试次数 + 健康状态上报），解决网络抖动导致的断连不恢复问题。
- 输入：risk_registry.md R34-2；TASK-0068 输出的 `vts_driver.py`。
- 输出：
  - `vts_driver.py` 新增 `_reconnect_loop()` 方法：指数退避（1s→2s→4s→8s→16s 上限）+ 最大重试 10 次；
  - 重连成功后自动恢复 `neutral` 表情状态并上报 `VTS_RECONNECTED` 事件；
  - 重连全部失败后进入 `DISCONNECTED` 状态并上报 `VTS_RECONNECT_EXHAUSTED` reason_code；
  - 重连事件写入 `avatar_action_log` 可审计；
  - fixtures：断连→重连成功/断连→重连耗尽。
- 验收标准：
  - 模拟 WebSocket 断连后自动触发重连；
  - 重连成功后表情映射恢复正常；
  - 超过最大重试次数后稳定进入 DISCONNECTED 状态且 reason_code 可统计；
  - 现有 T-VTS-HW-01 无回归；
  - 回归覆盖（新增）`T-VTS-Reconnect-01`（WebSocket 自动重连与状态恢复）。
- 涉及文件路径：edge_client/actuators/vts_driver.py；host_brain/core/errors.py；tests/contract/test_vts_reconnect_contract.py（新增）；tests/fixtures/channels/。
- 关联需求/测试：FR-AVATAR-001；T-VTS-HW-01；（新增）T-VTS-Reconnect-01。

### TASK-0081 ASR 麦克风设备自动恢复策略（P1 Edge 音频）
- 任务描述：为 ASR 音频采集添加设备异常自动恢复策略（重连重试 + 设备切换 + 健康状态上报），解决驱动异常时无法自动恢复的问题。
- 输入：risk_registry.md R33-2；TASK-0067 输出的 `asr.py`。
- 输出：
  - `asr.py` 新增 `_device_recovery_loop()` 方法：检测 `ASR_DEVICE_ERROR` / `ASR_STREAM_INTERRUPTED` 后自动尝试重新打开音频流；
  - 指数退避重试（1s→2s→4s→8s 上限）+ 最大重试 5 次；
  - 支持设备列表枚举 + 备用设备自动切换（如默认设备失败，尝试其他可用设备）；
  - 恢复成功上报 `ASR_DEVICE_RECOVERED` 事件；恢复失败上报 `ASR_DEVICE_RECOVERY_EXHAUSTED`；
  - fixtures：设备断开→恢复/设备断开→恢复失败/备用设备切换。
- 验收标准：
  - 模拟设备异常后自动触发恢复流程；
  - 恢复成功后 ASR 采集正常继续；
  - 超过最大重试次数后稳定报告 `ASR_DEVICE_RECOVERY_EXHAUSTED`；
  - 现有 T-ASR-HW-01 无回归；
  - 回归覆盖（新增）`T-ASR-Recovery-01`（麦克风设备自动恢复）。
- 涉及文件路径：edge_client/senses/asr.py；host_brain/core/errors.py；tests/contract/test_asr_recovery_contract.py（新增）；tests/fixtures/voice/。
- 关联需求/测试：FR-VOICE-001；FR-VOICE-005；T-ASR-HW-01；（新增）T-ASR-Recovery-01。

### TASK-0082 Host VTS 通道 DEGRADED 状态扩展（P2）
- 任务描述：为 Host 端 VTS 通道模块（`vts.py`）增加 `DEGRADED` 状态支持，使 E2E 链路中的降级场景可通过正规通道表达，而非直接写审计表绕过。
- 输入：risk_registry.md R37-2；TASK-0071 输出的 E2E 测试发现。
- 输出：
  - `vts.py` 的 `resolve_vts_expression()` 返回结构新增 `status=DEGRADED` 枚举值（在 Edge 不可达/表情映射失败但有降级备选时触发）；
  - 降级时使用 `neutral` 作为备选表情 + `VTS_DEGRADED_FALLBACK` reason_code；
  - 审计写入 `avatar_action_log` 记录降级详情；
  - fixtures：Edge 不可达→DEGRADED/映射未知→DEGRADED。
- 验收标准：
  - Edge 不可达时 `resolve_vts_expression` 返回 `DEGRADED` + neutral 备选；
  - `avatar_action_log` 可回溯降级原因；
  - 现有 T-VTS-HW-01 / T-E2E-WeChat-01 无回归；
  - 回归覆盖（新增）`T-VTS-Degraded-01`（VTS DEGRADED 状态契约）。
- 涉及文件路径：host_brain/channels/vts.py；host_brain/core/errors.py；tests/contract/test_vts_degraded_contract.py（新增）；tests/fixtures/channels/。
- 关联需求/测试：FR-AVATAR-001；T-VTS-HW-01；T-E2E-WeChat-01；（新增）T-VTS-Degraded-01。

### TASK-0083 部署文档 + 环境清单补充（P2 运维）
- 任务描述：基于风险注册表中所有部署相关的后续行动，补充部署文档与环境清单，确保生产部署前所有环境要求已明确。
- 输入：risk_registry.md 全文；代码审阅报告 §七。
- 输出：
  - `docs/50_project/ENV_EXTERNAL_SERVICES_CHECKLIST.md` 更新：
    - SQLite 存储介质要求（本地磁盘，禁止 NFS/网络文件系统）（R36-1）；
    - Redis 可选依赖说明与配置指引（R39-1）；
    - Docker 环境要求（沙箱容器级隔离）（R41-2）；
    - Edge GPU VRAM 最低要求（≥4GB CUDA 可用）+ 资源闸门配置（R33-1/R34-1）；
    - VTS WebSocket 网络要求与超时配置（R34-2）；
    - WAL checkpoint 定时策略建议（R36-3）；
    - 进程管理器（systemd/supervisor）守护建议（R31-2）。
  - 环境清单中每项标注：必需/可选/建议、关联风险编号、配置路径。
- 验收标准：
  - 所有 P1 风险的部署前置条件在环境清单中有对应条目；
  - 每项环境要求有验证方法（检查命令/健康端点/配置项）；
  - 文档符合受众门禁格式（受众：MASTER）。
- 涉及文件路径：docs/50_project/ENV_EXTERNAL_SERVICES_CHECKLIST.md。
- 关联需求/测试：NFR-REL-001；NFR-SEC-001。

---

### TASK-0084 Edge RPC Gateway 最小运行进程（P1 Edge 运行时）
- 任务描述：实现 Edge 侧最小可运行 RPC 网关进程，对 Host 暴露能力端点并完成签名/重放校验与契约化响应，确保“只执行、不裁决”。
- 输入：DevSpec 2.2（Edge RPC 能力端点）；DevSpec 2.4（Host→Edge HMAC + nonce + ts_ms）；DevSpec 14.2（Edge 边界）；TASK-0087 输出的鉴权与 schema；edge_client/config.py；edge_client/rpc/*。
- 前置依赖：TASK-0087（鉴权中间件）；TASK-0085（runtime 装配接口）。
- 输出：
  - `edge_client/app/fastapi_app.py`（新增）：提供 `POST /v1/edge/speak`、`POST /v1/edge/barge_in`、`POST /v1/edge/vts`、`POST /v1/edge/fallback/{presence|asr_compile|retrieval_hints|context_compress}`、`GET /v1/edge/health`（路由细节与参数以 edge_client/rpc 契约为准；asr 上送由 TASK-0088 统一上行实现）。
  - 统一响应结构：`{success,status,reason_code,detail,event,ts_ms}`，所有端点仅返回此结构。
  - 端点实现仅调用 edge_client/rpc 契约对象与 runtime 装配对象，不引入 Host 裁决逻辑与副作用外推。
  - `edge_client/pyproject.toml`：补充 FastAPI/uvicorn 依赖（Edge 侧 RPC 运行时）。
  - fixtures：端点缺字段/非法 payload/服务被 config 禁用的样例。
- 验收标准：
  - 被禁用能力端点返回稳定的不可用状态（reason_code 可统计）；
  - 所有 RPC 端点输出结构稳定（success/status/reason_code/detail/event），且拒绝原因与契约一致；
  - 回归覆盖（新增）`T-Edge-RPC-02`（端点契约）。
- 涉及文件路径：edge_client/app/fastapi_app.py（新增）；edge_client/pyproject.toml；tests/contract/test_edge_rpc_gateway_contract.py（新增）；tests/fixtures/edge/。
- 关联需求/测试：FR-VOICE-002~004；T-Edge-RPC-02。

### TASK-0085 Edge 运行时装配 + 健康快照（P1 Edge 观测）
- 任务描述：补齐 Edge 运行时装配器与健康快照聚合，返回能力矩阵与资源状态（显存/队列/活动任务）。
- 输入：DevSpec 2.2（health 能力矩阵与资源状态）；DevSpec 2.5（fallback LLM 约束）；edge_client/config.py；edge_client/senses/asr.py；edge_client/actuators/tts_engine.py；edge_client/actuators/vts_driver.py；edge_client/actuators/speech.py；edge_client/rpc/resource_gate.py；edge_client/rpc/fallback_llm.py。
- 前置依赖：TASK-0087（配置与安全字段统一）。
- 输出：
  - `edge_client/runtime/bootstrap.py`（新增）：根据 `EdgeConfig` 装配 ASR/TTS/VTS/Fallback/ResourceGate，统一生命周期与开关。
  - `edge_client/runtime/health.py`（新增）：聚合 `build_edge_runtime_statuses` + 各组件 `health()` + ResourceGate `snapshot()` + 队列深度，输出稳定 health schema。
  - health schema 必含：`services`（能力矩阵）、`resources`（vram_total_mb/vram_used_mb/queue_depth）、`components`（asr/tts/vts/fallback/resource_gate 详细状态）、`config_snapshot`（脱敏）。
  - `edge_client/app/fastapi_app.py` 的 `/v1/edge/health` 使用上述聚合结果。
  - fixtures：ASR/TTS 关闭、fallback 关闭、resource_gate 关闭的健康快照样例。
- 验收标准：
  - 配置开关与健康矩阵一致（禁用即不可用）；fallback 默认不可用；
  - 健康输出包含 services/资源/组件状态/配置快照，字段稳定且可审计；
  - 回归覆盖（新增）`T-Edge-Health-01`（能力矩阵与资源快照）。
- 涉及文件路径：edge_client/runtime/bootstrap.py（新增）；edge_client/runtime/health.py（新增）；edge_client/app/fastapi_app.py；edge_client/config.py；tests/contract/test_edge_health_contract.py（新增）；tests/fixtures/edge/。
- 关联需求/测试：FR-VOICE-001~005；T-Edge-Health-01。

### TASK-0086 Edge 启动入口 + 本地启动脚本（P2 运维）
- 任务描述：提供 Edge 的标准启动入口与本地脚本，确保可按环境变量直接启动/停止，且优雅释放音频与 VTS 连接。
- 输入：DevSpec 2.2（Edge 进程职责）；DevSpec 2.4（通信安全）；TASK-0085 输出的 runtime 装配；edge_client/config.py；README.md（运行指引）。
- 前置依赖：TASK-0084（RPC 网关）；TASK-0085（runtime 生命周期）。
- 输出：
  - `edge_client/__main__.py`（新增）：加载 EdgeConfig，初始化 runtime/bootstrap 并启动 RPC 网关；
  - `scripts/runtime/start_edge.ps1` / `scripts/runtime/stop_edge.ps1`（新增）：本地启动/停止 Edge 进程；
  - README.md：新增 Edge 启动说明与必要环境变量提示（含 `EDGE_RPC_HOST/PORT`、`EDGE_ALLOW_INSECURE_AUTH`、`EDGE_HMAC_SHARED_KEY(_REF)`）。
- 验收标准：
  - `python -m edge_client` 可在 `EDGE_RPC_HOST/PORT` 启动服务并输出脱敏配置快照；
  - Ctrl+C 能优雅关闭（ASR 音频流、TTS 播放、VTS 连接关闭，无资源泄漏）；
  - 回归覆盖（新增）`T-Edge-Start-01`（启动/关闭最小烟测）。
- 涉及文件路径：edge_client/__main__.py（新增）；edge_client/runtime/bootstrap.py；scripts/runtime/start_edge.ps1（新增）；scripts/runtime/stop_edge.ps1（新增）；README.md；tests/contract/test_edge_bootstrap_contract.py（新增）。
- 关联需求/测试：FR-VOICE-001~005；T-Edge-Start-01。

### TASK-0087 Edge RPC 请求包络 + 鉴权与重放保护（P1 Edge 安全）
- 任务描述：定义 Edge RPC 统一请求包络与鉴权/重放校验策略，提供稳定的拒绝 reason_code 与可回归 fixtures。
- 输入：DevSpec 2.4（HMAC + nonce + ts_ms + 重放窗口）；DevSpec 14.3（强类型 Schema）；edge_client/config.py；host_brain/core/errors.py。
- 前置依赖：无（Edge 阶段基线任务）。
- 输出：
  - `edge_client/app/schemas.py`（新增）：定义 `EdgeRpcEnvelope`（request_id/ts_ms/nonce/signature/payload）与 `EdgeRpcResponse`（success/status/reason_code/detail/event/ts_ms）。
  - `edge_client/rpc/auth.py`（新增）：实现 HMAC 校验、时间窗校验、nonce 重放缓存；提供可注入的 `now_ms()` 与缓存清理策略。
  - `edge_client/config.py`：新增鉴权相关配置项（如 `EDGE_RPC_AUTH_WINDOW_MS`/`EDGE_RPC_NONCE_WINDOW_MS`），并进入 config snapshot。
  - `host_brain/core/errors.py`：新增鉴权/Schema 失败类 reason_code（如 `EDGE_RPC_SCHEMA_INVALID`/`EDGE_RPC_AUTH_INVALID`/`EDGE_RPC_AUTH_EXPIRED`），与 `REPLAY_DETECTED` 并列。
  - fixtures：缺字段/签名错误/ts_ms 过期/nonce 重放/允许不鉴权（dev/test）样例。
- 验收标准：
  - 缺字段稳定返回 `EDGE_RPC_SCHEMA_INVALID`；签名错误返回 `EDGE_RPC_AUTH_INVALID`；ts_ms 过期返回 `EDGE_RPC_AUTH_EXPIRED`；nonce 重放返回 `REPLAY_DETECTED`；
  - `EDGE_ALLOW_INSECURE_AUTH=true` 时仍校验 schema，但允许跳过签名校验并记录降级字段；
  - 回归覆盖（新增）`T-Edge-RPC-01`（鉴权与重放）。
- 涉及文件路径：edge_client/app/schemas.py（新增）；edge_client/rpc/auth.py（新增）；edge_client/config.py；host_brain/core/errors.py；tests/contract/test_edge_rpc_auth_contract.py（新增）；tests/fixtures/edge/。
- 关联需求/测试：FR-CORE-002；NFR-SEC-001；T-Edge-RPC-01。

### TASK-0088 Edge 语音上行编排与事件上送（P1 Edge 语音）
- 任务描述：补齐 Edge 语音链路的编排层，将 ASR 事件稳定上送到 Host，并与 TTS 播放/打断/VTS 表情联动。
- 输入：DevSpec 2.2（asr/speak/barge-in/vts/health）；DevSpec 2.5（fallback 约束）；edge_client/senses/asr.py；edge_client/actuators/speech.py；edge_client/actuators/tts_engine.py；edge_client/actuators/vts_driver.py；edge_client/rpc/event_reporter.py；edge_client/rpc/voice.py。
- 前置依赖：TASK-0079（Edge→Host 事件上报）；TASK-0084（RPC 网关）；TASK-0085（runtime 装配）。
- 输出：
  - `edge_client/runtime/voice_pipeline.py`（新增）：封装 ASR→事件上送、TTS 队列、barge-in 打断、VTS talk gate 联动。
  - ASR 上行：使用 `EdgeEventReporter` 将 asr_chunk 作为 EdgeEvent 批量上送（对接 Host `POST /v1/edge/events`），附带 vram/queue 指标（若可用）。
  - 播放联动：TTS 播放开始触发 VTS talk_start，结束/打断触发 talk_end 或 neutral 复位。
  - 资源闸门：speak/tts/playback/llm 使用 `VoiceResourceGate`，优先级与互斥符合 DevSpec。
  - fixtures：ASR 正常上送/网络中断缓存/恢复补发；barge-in 打断播放与 VTS 复位。
- 验收标准：
  - ASR 事件可通过 Edge→Host 事件通道稳定上送并通过 schema 校验；
  - barge-in 可打断播放并记录 `VOICE_INTERRUPTED`，VTS 进入结束态；
  - 资源闸门生效时返回稳定拒绝 reason_code；
  - 回归覆盖（新增）`T-Edge-Voice-01`（ASR 上送与缓存补发）与 `T-Edge-Voice-02`（barge-in 与 VTS 联动）。
- 涉及文件路径：edge_client/runtime/voice_pipeline.py（新增）；edge_client/runtime/bootstrap.py；edge_client/actuators/speech.py；edge_client/rpc/event_reporter.py；tests/contract/test_edge_voice_pipeline_contract.py（新增）；tests/fixtures/edge/。
- 关联需求/测试：FR-VOICE-001~003；FR-VOICE-005；T-Edge-Voice-01；T-Edge-Voice-02。

### TASK-0089 Edge Contract 测试矩阵补齐与 fixtures 命名规范（P1 QA）
- 任务描述：将 Edge 新增任务（0084~0088）的验收断言固化为可追溯 contract 测试矩阵，并统一 fixtures 命名与目录分层，避免后续测试口径漂移。
- 输入：TASK-0084~0088 的输出契约；tests/TESTPLAN.md；tests/contract 既有风格。
- 前置依赖：TASK-0084~0088（功能接口已落地）。
- 输出：
  - `tests/contract/` 新增或完善测试文件：`test_edge_rpc_auth_contract.py`、`test_edge_rpc_gateway_contract.py`、`test_edge_health_contract.py`、`test_edge_bootstrap_contract.py`、`test_edge_voice_pipeline_contract.py`。
  - fixtures 命名规范：`tests/fixtures/edge/{auth|gateway|health|voice}/{good|bad}/*.json`，文件名采用 `场景_期望.json`（例：`expired_ts_reject.json`）。
  - 覆盖映射：每个 `T-Edge-*` 编号至少有一个成功样例和一个拒绝/降级样例。
  - `tests/TESTPLAN.md` 同步登记 `T-Edge-RPC-01`、`T-Edge-RPC-02`、`T-Edge-Health-01`、`T-Edge-Start-01`、`T-Edge-Voice-01`、`T-Edge-Voice-02` 的断言口径。
- 验收标准：
  - `DevTasks` 中所有 `T-Edge-*` 编号在 `TESTPLAN` 可检索且语义一致；
  - 合同测试可覆盖鉴权失败、schema 失败、能力禁用、启动关闭、事件补发与打断联动；
  - fixtures 目录结构与命名规范稳定，新增场景不破坏既有回放路径。
- 涉及文件路径：tests/contract/；tests/fixtures/edge/；tests/TESTPLAN.md；docs/30_devspec/DevTasks.md。
- 关联需求/测试：FR-CORE-002；FR-VOICE-001~005；T-Edge-RPC-01；T-Edge-RPC-02；T-Edge-Health-01；T-Edge-Start-01；T-Edge-Voice-01；T-Edge-Voice-02。

### TASK-0090 Edge 交付顺序与回滚口径固化（P2 交付）
- 任务描述：固化 Edge 任务分批交付顺序、失败回滚策略与最小可运行门槛，确保 0084~0089 可逐批上线且可快速回退。
- 输入：TASK-0084~0089；docs/50_project/WORKSHEET_Project_Codex_Git_Flow.md；README.md。
- 前置依赖：TASK-0089（测试矩阵完成）。
- 输出：
  - 交付批次定义（建议）：`Batch-A`=0087+0085、`Batch-B`=0084+0086、`Batch-C`=0088+0089；
  - 每批回滚口径：回滚触发条件、保留资产（fixtures/audit）、回滚后可用能力矩阵；
  - README 增加“Edge 最小可运行门槛”与“故障回退模式（仅 health / 禁用 speak / 禁用 asr 上行）”说明。
- 验收标准：
  - 任一批次失败时有明确回滚目标与可验证健康状态；
  - 回滚不破坏审计回放链和 `T-RPC-Event-01` 既有能力；
  - 交付文档与 `DevTasks`/`TESTPLAN` 编号一致。
- 涉及文件路径：docs/50_project/WORKSHEET_Project_Codex_Git_Flow.md；README.md；docs/30_devspec/DevTasks.md；tests/TESTPLAN.md。
- 关联需求/测试：NFR-REL-001；FR-CORE-004；T-Edge-Start-01；T-RPC-Event-01。

## 4. M4 体验包任务清单（TASK-0091 ~ TASK-0098）

### TASK-0091 Scheduler 定时调度器基础组件（M4 前置，P0）
- 任务描述：实现 Host 定时调度器基础组件，支持 cron/interval/once 三种触发模式；调度器触发的 Intent 必须走完整单向链（Governor→Token→Skill→Audit），与用户请求享有相同治理。
- 输入：DevSpec 15.1；PRD FR-SCHED-001~002。
- 输出：
  - `host_brain/scheduler/scheduler.py`（新增）：调度器主体，基于 APScheduler 或等价轻量库；
  - `host_brain/scheduler/task_registry.py`（新增）：任务注册表（task_id/trigger_config/intent_template/priority/mode_allowlist/enabled/max_consecutive_failures）；
  - `host_brain/scheduler/health.py`（新增）：任务健康快照（task_id/last_run_ts/next_run_ts/status/error_count/suspended_reason）；
  - `scheduler_event_log` 审计表（SQLite Ledger 新增）：task_id/trigger_ts/intent_id/status/reason_code/mode_at_trigger/error_detail；
  - `system_initiated_intent` 生成逻辑：proposer_node_id=scheduler，复用现有 Intent Schema；
  - sys_mode 门控矩阵实现（HIGH/MEDIUM/LOW 优先级 × Normal/Sleep/Gaming/Focus）；
  - 连续失败自动暂停策略（max_consecutive_failures 后 status=SUSPENDED + SCHED_TASK_SUSPENDED）；
  - 新增错误码：SCHED_TASK_SUSPENDED / SCHED_CONDITION_UNMET / SCHED_NO_CONTENT；
  - fixtures：正常触发/Gaming 拦截/连续失败暂停/Focus 模式暂停。
- 验收标准：
  - 调度器注册/注销/触发可回放，审计链中 proposer_node_id=scheduler；
  - sys_mode=GAMING 时低/中优先级任务被拦截（SYSMODE_FORBIDDEN）；
  - 连续失败 N 次后自动暂停且 reason_code 可统计；
  - 健康快照可被 /health 端点或面板读取；
  - T-Sched-01、T-Sched-02、T-Sched-03 全部通过。
- 涉及文件路径：host_brain/scheduler/scheduler.py（新增）；host_brain/scheduler/task_registry.py（新增）；host_brain/scheduler/health.py（新增）；host_brain/audit/sqlite_ledger.py；host_brain/core/errors.py；tests/contract/test_scheduler_contract.py（新增）；tests/fixtures/scheduler/。
- 关联需求/测试：FR-SCHED-001~002；FR-MODE-001；T-Sched-01~03。

### TASK-0092 Desktop Note Skill 桌面留言（M4 P0）
- 任务描述：实现桌面留言 Skill，支持在安全基目录下创建/更新/删除文本文件；操作可逆，产出 manifest + undo_token；路径穿越拒绝；内容经 QA 校验。
- 输入：DevSpec 15.2；PRD FR-DESKTOP-001~002；TASK-0084 输出的 Scheduler（用户触发为主，Scheduler 触发为辅）。
- 输出：
  - `host_brain/skills/impl/desktop_note.py`（新增）：桌面留言 Skill handler + SkillMeta 声明；
  - args 校验：action/filename/content/base_dir_key；路径穿越检查（`..`/symlink 拒绝 → SCOPE_VIOLATION）；
  - 文件 manifest：before_hash/after_hash/path/artifact_refs；
  - delete → move-to-quarantine；真删需 confirm_token；
  - QA 校验集成：内容含伪执行句 → 拦截重写；
  - 新增配置项：NATSUME_DESKTOP_DIR（安全基目录）；
  - fixtures：创建成功/路径穿越拒绝/undo 恢复/QA 拦截/Gaming 拒绝。
- 验收标准：
  - 文件创建成功 + manifest 完整 + undo 恢复原状态；
  - 路径穿越 → SCOPE_VIOLATION；Gaming → SYSMODE_FORBIDDEN；
  - 内容 QA 拦截可回放；
  - T-Desktop-01、T-Desktop-02 全部通过。
- 涉及文件路径：host_brain/skills/impl/desktop_note.py（新增）；host_brain/skills/registry.py；host_brain/lsp/vault.py；host_brain/core/qa.py；tests/contract/test_desktop_note_contract.py（新增）；tests/fixtures/desktop/。
- 关联需求/测试：FR-DESKTOP-001~002；FR-LSP-001~002；T-Desktop-01~02；T-Vault-01。

### TASK-0093 Dream Skill 梦境生成（M4 P1）
- 任务描述：实现梦境生成 Skill，由调度器在 Sleep/空闲时触发；读取当日 Episodic Memory → LLM 生成梦境叙事 → 写入 Episodic（dream 标签 + NARRATIVE_ONLY）。
- 输入：DevSpec 15.3；PRD FR-DREAM-001~002；TASK-0084 输出的 Scheduler。
- 输出：
  - `host_brain/skills/impl/dream_generator.py`（新增）：梦境 Skill handler + SkillMeta 声明；
  - Scheduler 任务注册：trigger=Sleep/idle，priority=MEDIUM；
  - 当日 Episodic 读取逻辑（ts_ms 当日范围过滤，TopK=20）；
  - context_pack 打包（bond_summary + 当日 episodic 摘要）→ LLM 梦境叙事；
  - Episodic 写入：category=dream, evidence_level=NARRATIVE_ONLY, evidence_refs=[intent_id]；
  - manifest：memory_before_count/memory_after_count/dream_id/hash；
  - QA 校验：不得伪执行/Canon-as-fact；
  - 召回口径扩展：episodic 中 evidence_level=NARRATIVE_ONLY 标注在 context_pack 透传；
  - fixtures：Sleep 模式触发成功/Normal 非空闲拒绝/Episodic 不可用降级/梦境召回非事实约束。
- 验收标准：
  - 梦境写入 Episodic 带 dream 标签 + NARRATIVE_ONLY；
  - Normal 非空闲时拒绝（SCHED_CONDITION_UNMET）；
  - 召回梦境时 QA 确认 evidence_level 标注存在；
  - T-Dream-01、T-Dream-02 全部通过。
- 涉及文件路径：host_brain/skills/impl/dream_generator.py（新增）；host_brain/skills/registry.py；host_brain/memory/episodic.py；host_brain/core/context_pack.py；host_brain/core/qa.py；tests/contract/test_dream_contract.py（新增）；tests/fixtures/dream/。
- 关联需求/测试：FR-DREAM-001~002；FR-MEM-001；FR-SCHED-001；T-Dream-01~02。

### TASK-0094 Diary Skill 日记生成（M4 P1）
- 任务描述：实现每日日记 Skill，由调度器定时触发；汇总当日对话 → LLM 生成日记叙事 → 双写文件 + Episodic Memory；无对话时跳过不伪造。
- 输入：DevSpec 15.4；PRD FR-DIARY-001~002；TASK-0084 输出的 Scheduler。
- 输出：
  - `host_brain/skills/impl/diary_writer.py`（新增）：日记 Skill handler + SkillMeta 声明；
  - Scheduler 任务注册：trigger=cron(23:00)，priority=HIGH；
  - 当日对话汇总逻辑：读取 intent_log + governor_log 当日记录 + Episodic 当日条目；
  - 无实质对话检测 → 跳过 + SCHED_NO_CONTENT（不伪造日记）；
  - 有对话 → context_pack → LLM 日记叙事 → 双写：
    - 本地文件：`{base_dir}/diary/{YYYY-MM-DD}.md`，产出 file manifest；
    - Episodic Memory：category=diary, evidence_refs=[intent_id, file_path]；
  - QA 校验：不得含伪执行句；
  - Evolution 提案（可选）：drift_suggestion → canon_proposal_log；
  - fixtures：有对话触发成功/无对话跳过/文件 undo 回滚/Evolution 提案写入。
- 验收标准：
  - 日记文件创建 + Episodic 写入 + 审计链完整；
  - 无当日对话时不伪造（SCHED_NO_CONTENT）；
  - 日记文件 manifest 可撤销；
  - T-Diary-01、T-Diary-02 全部通过。
- 涉及文件路径：host_brain/skills/impl/diary_writer.py（新增）；host_brain/skills/registry.py；host_brain/memory/episodic.py；host_brain/audit/sqlite_ledger.py；host_brain/lsp/vault.py；tests/contract/test_diary_contract.py（新增）；tests/fixtures/diary/。
- 关联需求/测试：FR-DIARY-001~002；FR-MEM-001；FR-LSP-001；FR-SCHED-001；T-Diary-01~02。

### TASK-0095 Proactive Talk Skill 主动对话（M4 P1）
- 任务描述：实现主动对话 Skill，由调度器按条件策略触发；生成问候/关心/提醒内容并通过 Edge RPC 或微信通道推送；频率限制 + 内容 QA + 推送失败静默降级。
- 输入：DevSpec 15.5；PRD FR-PROACTIVE-001~002；TASK-0084 输出的 Scheduler。
- 输出：
  - `host_brain/skills/impl/proactive_talk.py`（新增）：主动对话 Skill handler + SkillMeta 声明；
  - Scheduler 任务注册：trigger=interval(15min check)，priority=LOW；
  - 触发条件组合实现：idle_duration + bond.stage + sys_mode + active_hours（AND 逻辑）；
  - 频率限制集成：Rate Limit R4 Gate（3/hour）；
  - context_pack 打包（bond_summary + 最近 episodic + 时间段信息）→ LLM 草案；
  - QA 校验：内容仅限问候/关心/回忆引用/提醒口径；含执行 intent → 拦截；
  - 推送路径选择：Edge RPC speak > 微信 outbound > 静默降级（不重试）；
  - 审计：复用 skill_exec_log 记录触发条件快照、推送结果、reason_code；
  - fixtures：空闲触发成功/Gaming 拒绝/频率超限/Edge 不可达降级/内容 QA 拦截。
- 验收标准：
  - 空闲触发 + 推送成功 + 审计链完整；
  - Gaming → SYSMODE_FORBIDDEN；频率超限 → RATE_LIMIT_EXCEEDED；
  - 推送失败静默降级不重试；
  - T-Proactive-01、T-Proactive-02 全部通过。
- 涉及文件路径：host_brain/skills/impl/proactive_talk.py（新增）；host_brain/skills/registry.py；host_brain/channels/wechat.py；host_brain/core/qa.py；host_brain/governor/rules.py；tests/contract/test_proactive_contract.py（新增）；tests/fixtures/proactive/。
- 关联需求/测试：FR-PROACTIVE-001~002；FR-SCHED-001；FR-CHANNEL-001；T-Proactive-01~02。

### TASK-0096 Focus Mode 专注模式状态扩展（M4 P1）
- 任务描述：扩展 sys_mode 状态机，新增 Focus 子模式（Normal 的子状态）；进入后暂停中断型能力（主动对话/通知推送/非紧急调度），保留被动响应；进入/退出可审计。
- 输入：DevSpec 15.6；PRD FR-FOCUS-001；TASK-0084 输出的 Scheduler 门控矩阵。
- 输出：
  - `host_brain/core/metabolism.py` 扩展：新增 sub_mode=FOCUS 状态枚举与切换逻辑；
  - 进入条件：用户指令 / 定时策略 / 日历事件（需 evidence）；
  - 退出条件：用户指令 / 定时策略到期 / 超时自动退出（可配置默认时长）；
  - Governor rules.py 扩展：Focus 子模式下 proactive/dream 等 LOW/MEDIUM 优先级任务 → SYSMODE_FORBIDDEN_FOCUS；
  - Scheduler 联动：Focus 进入时暂停非紧急任务，退出时恢复；
  - hard_state_log 审计：进入/退出事件（原因、证据摘要、持续时长）；
  - scheduler_event_log：被拦截任务写 reason_code=SYSMODE_FORBIDDEN_FOCUS；
  - fixtures：进入 Focus/主动对话拦截/用户提问正常/退出恢复/超时自动退出。
- 验收标准：
  - Focus 进入后主动对话被拦截；用户主动提问正常响应；
  - 退出后调度器恢复；审计可回放切换事件；
  - T-Focus-01 全部通过。
- 涉及文件路径：host_brain/core/metabolism.py；host_brain/governor/rules.py；host_brain/scheduler/scheduler.py；host_brain/audit/sqlite_ledger.py；tests/contract/test_focus_mode_contract.py（新增）；tests/fixtures/focus/。
- 关联需求/测试：FR-FOCUS-001；FR-MODE-001~002；FR-SCHED-001；T-Focus-01。

### TASK-0097 M4 E2E 集成测试：Scheduler→Skill→Audit 全链闭环验证（M4 P2）
- 任务描述：为 M4 全部 5 个 Skill（desktop_note/dream_generator/diary_writer/proactive_talk/focus_mode）编写 E2E 集成测试，验证从 Scheduler 触发到 Audit 写入的完整闭环。
- 输入：TASK-0084~TASK-0089 输出的所有组件。
- 输出：
  - `tests/e2e/test_m4_experience_pack.py`（新增）：
    - 桌面留言 E2E：创建 → 验证文件 → undo → 验证恢复 → 审计串联；
    - 梦境 E2E：Sleep 触发 → Episodic 写入 dream 标签 → 后续对话召回 → QA 非事实约束；
    - 日记 E2E：定时触发 → 文件 + Episodic 双写 → 无对话跳过 → 审计串联；
    - 主动对话 E2E：空闲触发 → 推送成功 → 频率超限拒绝 → Gaming 拒绝 → 审计串联；
    - Focus Mode E2E：进入 → 主动对话拦截 → 用户提问正常 → 退出恢复 → 审计串联；
  - 所有 E2E 使用 LLM stub + 本地 fixtures，不依赖真实外部服务。
- 验收标准：
  - 5 个功能的 E2E 全部通过；
  - 现有 E2E 测试无回归；
  - 审计链串联键（request_id/intent_id/proposer_node_id）完整可回放。
- 涉及文件路径：tests/e2e/test_m4_experience_pack.py（新增）；tests/fixtures/scheduler/；tests/fixtures/desktop/；tests/fixtures/dream/；tests/fixtures/diary/；tests/fixtures/proactive/；tests/fixtures/focus/。
- 关联需求/测试：FR-SCHED-001~002；FR-DESKTOP-001~002；FR-DREAM-001~002；FR-DIARY-001~002；FR-PROACTIVE-001~002；FR-FOCUS-001；T-Sched-01~03；T-Desktop-01~02；T-Dream-01~02；T-Diary-01~02；T-Proactive-01~02；T-Focus-01。

### TASK-0098 性能预算基准：全链路时延采集 + p50/p95 报告 + 超时退化验证（P1）
- 任务描述：实现全链路时延采集点、bench 脚本聚合报告、以及超时退化策略的验证，使 NFR-PERF-001 从定性描述变为可量化基线。
- 输入：DevSpec 16；PRD NFR-PERF-001；现有 tools/bench/ledger_bench.py（Ledger 层基准）。
- 输出：
  - `host_brain/core/latency.py`（新增）：时延采集点注入（request_start_ms → response_end_ms）与 latency_breakdown 结构化输出；
  - `host_brain/audit/sqlite_ledger.py` 扩展：latency_breakdown JSON 字段写入审计链；
  - `host_brain/core/errors.py` 扩展：PERF_TIMEOUT / PERF_LLM_TIMEOUT / PERF_RETRIEVAL_TIMEOUT / PERF_SKILL_TIMEOUT 错误码；
  - `tools/bench/e2e_latency_bench.py`（新增）：全链路 p50/p95/p99 聚合报告脚本（复用 LLM stub + 本地 fixtures）；
  - `tools/bench/retrieval_latency_bench.py`（新增）：检索链路 p50/p95 聚合报告脚本；
  - 超时退化策略集成：LLM/检索/Skill 超时时输出结构稳定 + quality.stage 标注 + reason_code 可统计；
  - fixtures：LLM 超时/检索超时/Skill 超时/正常基准样例。
- 验收标准：
  - 基准机器上各阶段 p50/p95 在 DevSpec 16.1 目标范围内；
  - 模拟超时场景触发退化策略，输出结构稳定且 quality.stage/reason_code 可统计；
  - Ledger 写入 p95 ≤ 20ms（复用 ledger_bench.py）；
  - T-Perf-01 全部通过。
- 涉及文件路径：host_brain/core/latency.py（新增）；host_brain/core/errors.py；host_brain/audit/sqlite_ledger.py；tools/bench/e2e_latency_bench.py（新增）；tools/bench/retrieval_latency_bench.py（新增）；tests/contract/test_latency_contract.py（新增）；tests/fixtures/perf/。
- 关联需求/测试：NFR-PERF-001；T-Perf-01；T-Audit-01。

---

## M7 可观测 + MCP + 语音深化（TASK-0099~0106）

> 对应 DevSpec §17；PRD §4.16（FR-OBS）、§4.18（FR-MCP）、§4.19（FR-VOICE）。
> 所有任务当前状态：DONE（2026-03-03 实现，914 passed）。

### TASK-0099 可观测指标面板：/obs/metrics 接口 + request_id 串联（M7 P0）
- 任务描述：实现 `/obs/metrics` HTTP 接口，聚合 Governor 裁决计数、伪执行拦截次数、检索 p50/p95、Hard Snapshot staleness、最近链路摘要，并保证所有指标可通过 request_id 串联到 SQLite Ledger。
- 输入：DevSpec §17.1.1；host_brain/audit/sqlite_ledger.py；host_brain/governor/rules.py；host_brain/core/metabolism.py。
- 输出：
  - `host_brain/obs/metrics.py`（新增）：MetricsCollector 类，从 Ledger 聚合各项指标；
  - `host_brain/obs/__init__.py`（新增）：包初始化；
  - `host_brain/obs/routes.py`（新增）：Flask/FastAPI 路由 `GET /obs/metrics`，返回 DevSpec §17.1.1 定义的 JSON 结构；
  - `host_brain/audit/sqlite_ledger.py` 扩展：新增 `query_governor_stats()`、`query_recent_chains(n)` 查询方法；
  - `tests/contract/test_obs_metrics_contract.py`（新增）：T-OBS-01 验收测试（结构稳定 + request_id 串联可回放）；
  - `tests/fixtures/obs/`（新增）：metrics 查询 fixtures。
- 验收标准：
  - `/obs/metrics` 返回结构与 DevSpec §17.1.1 完全一致；
  - `governor_decisions` 计数与 Ledger 中 `governor_decision_log` 一致；
  - `recent_chains` 中每条记录的 `request_id` 可在 Ledger 中找到对应审计行；
  - T-OBS-01 全部通过。
- 涉及文件路径：host_brain/obs/metrics.py（新增）；host_brain/obs/__init__.py（新增）；host_brain/obs/routes.py（新增）；host_brain/audit/sqlite_ledger.py；tests/contract/test_obs_metrics_contract.py（新增）；tests/fixtures/obs/。
- 关联需求/测试：FR-OBS-001；T-OBS-01。

### TASK-0100 可观测模式差异：dev/prod 模式切换 + debug_trace 字段控制（M7 P1）
- 任务描述：实现 `OBS_MODE=dev|prod` 环境变量控制，dev 模式下 `/obs/metrics` 额外返回 `debug_trace`（含 LLM intent_draft、Governor 规则命中路径、Skill 执行参数），prod 模式下不返回。
- 输入：DevSpec §17.1.2；TASK-0099 输出的 MetricsCollector + routes.py。
- 输出：
  - `host_brain/obs/metrics.py` 扩展：`ObsMode` 枚举（DEV/PROD）+ `get_obs_mode()` 读取环境变量；
  - `host_brain/obs/routes.py` 扩展：根据 ObsMode 决定是否附加 `debug_trace` 字段；
  - `host_brain/core/errors.py` 扩展：新增 `OBS_MODE_INVALID` 错误码；
  - `tests/contract/test_obs_mode_contract.py`（新增）：T-OBS-02 验收测试（切换模式后 debug_trace 字段按预期出现/消失；不影响裁决与审计事实）；
  - `tests/fixtures/obs/` 扩展：dev_mode_response.json / prod_mode_response.json。
- 验收标准：
  - `OBS_MODE=dev` 时 `debug_trace` 字段存在且包含 intent_draft/rules_hit/skill_params；
  - `OBS_MODE=prod` 时 `debug_trace` 字段为 null 或不存在；
  - 切换模式不影响 Governor 裁决结果与 Ledger 审计写入；
  - T-OBS-02 全部通过。
- 涉及文件路径：host_brain/obs/metrics.py；host_brain/obs/routes.py；host_brain/core/errors.py；tests/contract/test_obs_mode_contract.py（新增）；tests/fixtures/obs/。
- 关联需求/测试：FR-OBS-002；T-OBS-02。

### TASK-0101 MCP ToolMeta 注册表：同步 + 分级 + 变更留痕（M7 P0）
- 任务描述：实现 MCP provider 工具元数据（ToolMeta）的同步注册表，包含 access_level/risk_level/reversible 分级字段，变更写 `mcp_sync_log`（可回放）。
- 输入：DevSpec §17.2.1；host_brain/audit/sqlite_ledger.py；host_brain/skills/registry.py。
- 输出：
  - `host_brain/mcp/__init__.py`（新增）：包初始化；
  - `host_brain/mcp/tool_registry.py`（新增）：ToolMeta 数据类 + MCPToolRegistry 注册表（SQLite 持久化 `mcp_tool_registry` 表）；
  - `host_brain/audit/sqlite_ledger.py` 扩展：新增 `mcp_tool_registry` 表 + `mcp_sync_log` 表 + `log_mcp_sync()` 方法；
  - `host_brain/audit/migrations/0015_mcp_tool_schema.sql`（新增）：建表 SQL；
  - `tests/contract/test_mcp_registry_contract.py`（新增）：ToolMeta 注册/更新/变更留痕验收测试；
  - `tests/fixtures/mcp/`（新增）：tool_meta_read.json / tool_meta_write.json / sync_log.json。
- 验收标准：
  - ToolMeta 注册后可按 provider_id/tool_id 查询；
  - 更新 ToolMeta 时 `mcp_sync_log` 写入变更记录（含 before/after diff）；
  - `access_level`/`risk_level`/`reversible` 字段完整且类型正确；
  - 注册表测试全部通过。
- 涉及文件路径：host_brain/mcp/__init__.py（新增）；host_brain/mcp/tool_registry.py（新增）；host_brain/audit/sqlite_ledger.py；host_brain/audit/migrations/0015_mcp_tool_schema.sql（新增）；tests/contract/test_mcp_registry_contract.py（新增）；tests/fixtures/mcp/。
- 关联需求/测试：FR-MCP-001；T-MCP-01~03。

### TASK-0102 MCP ToolBroker：单入口调用 + Governor 门禁 + Token 治理 + 审计（M7 P0）
- 任务描述：实现 ToolBroker 单入口，强制 `intent_draft.mcp_call → ToolBroker → Governor gate → Token（WRITE 必须）→ MCP SDK → Audit` 调用链，WRITE 无 token 拒绝，provider 不可达降级。
- 输入：DevSpec §17.2.2；TASK-0101 输出的 MCPToolRegistry；host_brain/governor/rules.py；host_brain/core/token_ledger.py；host_brain/audit/sqlite_ledger.py。
- 输出：
  - `host_brain/mcp/tool_broker.py`（新增）：ToolBroker 类，实现完整调用链；
  - `host_brain/audit/sqlite_ledger.py` 扩展：新增 `mcp_exec_log` 表 + `log_mcp_exec()` 方法；
  - `host_brain/audit/migrations/0015_mcp_tool_schema.sql` 扩展：`mcp_exec_log` 建表 SQL；
  - `host_brain/core/errors.py` 扩展：`MCP_TOOL_NOT_REGISTERED`、`MCP_WRITE_TOKEN_REQUIRED`、`MCP_PROVIDER_UNAVAILABLE` 错误码；
  - `tests/contract/test_mcp_broker_contract.py`（新增）：T-MCP-01~03 验收测试；
  - `tests/fixtures/mcp/` 扩展：write_no_token.json / read_no_token.json / provider_unavailable.json。
- 验收标准：
  - WRITE 无 token → `MCP_WRITE_TOKEN_REQUIRED` 拒绝 + `mcp_exec_log` 写入 decision=REJECT；
  - READ 无 token → 正常执行 + `mcp_exec_log` 写入 decision=APPROVE；
  - provider 不可达 → `MCP_PROVIDER_UNAVAILABLE` 降级 + 输出结构稳定；
  - T-MCP-01~03 全部通过。
- 涉及文件路径：host_brain/mcp/tool_broker.py（新增）；host_brain/audit/sqlite_ledger.py；host_brain/audit/migrations/0015_mcp_tool_schema.sql；host_brain/core/errors.py；tests/contract/test_mcp_broker_contract.py（新增）；tests/fixtures/mcp/。
- 关联需求/测试：FR-MCP-001；T-MCP-01~03。

### TASK-0103 ASR 事件化上送：事件 Schema + 低置信度标注 + 审计（M7 P1）
- 任务描述：实现 ASR 事件化上送协议（Edge→Host RPC），包含 confidence/is_final/alternates 字段，低置信度（<0.6）标注 `low_confidence=true`，事件写 `voice_event_log`。
- 输入：DevSpec §17.3.1；edge_client/actuators/vts_driver.py（参考 RPC 模式）；host_brain/audit/sqlite_ledger.py。
- 输出：
  - `host_brain/voice/__init__.py`（新增）：包初始化；
  - `host_brain/voice/asr_handler.py`（新增）：ASREvent 数据类 + ASRHandler（接收事件、低置信度标注、写 voice_event_log）；
  - `host_brain/audit/sqlite_ledger.py` 扩展：新增 `voice_event_log` 表 + `log_voice_event()` 方法；
  - `host_brain/audit/migrations/0016_voice_event_schema.sql`（新增）：建表 SQL；
  - `host_brain/core/errors.py` 扩展：`ASR_LOW_CONFIDENCE`、`ASR_UNAVAILABLE` 错误码；
  - `tests/contract/test_asr_contract.py`（新增）：T-VOICE-01 验收测试；
  - `tests/fixtures/voice/`（新增）：asr_high_confidence.json / asr_low_confidence.json / asr_unavailable.json。
- 验收标准：
  - confidence ≥ 0.6 → 正常处理，`voice_event_log` 写入 `low_confidence=false`；
  - confidence < 0.6 → `low_confidence=true` + `reason_code=ASR_LOW_CONFIDENCE`；
  - ASR 不可用 → `ASR_UNAVAILABLE` + 输出结构稳定；
  - T-VOICE-01 全部通过。
- 涉及文件路径：host_brain/voice/__init__.py（新增）；host_brain/voice/asr_handler.py（新增）；host_brain/audit/sqlite_ledger.py；host_brain/audit/migrations/0016_voice_event_schema.sql（新增）；host_brain/core/errors.py；tests/contract/test_asr_contract.py（新增）；tests/fixtures/voice/。
- 关联需求/测试：FR-VOICE-001；T-VOICE-01。

### TASK-0104 TTS 合成播报队列：优先级队列 + 打断 + 失败可解释（M7 P1）
- 任务描述：实现 TTS 下发协议（Host→Edge RPC）与播报队列，按 priority 排序，`interruptible=true` 的任务可被 Barge-in 打断，播报完成/打断后写 `voice_event_log`。
- 输入：DevSpec §17.3.2；TASK-0103 输出的 voice_event_log；host_brain/audit/sqlite_ledger.py。
- 输出：
  - `host_brain/voice/tts_queue.py`（新增）：TTSTask 数据类 + TTSQueue（优先级队列 + 打断逻辑）；
  - `host_brain/audit/sqlite_ledger.py` 扩展：`log_voice_event()` 支持 TTS 事件类型；
  - `host_brain/core/errors.py` 扩展：`TTS_QUEUE_FULL`、`TTS_INTERRUPTED` 错误码；
  - `tests/contract/test_tts_contract.py`（新增）：T-VOICE-02 验收测试；
  - `tests/fixtures/voice/` 扩展：tts_normal.json / tts_queue_full.json / tts_interrupted.json。
- 验收标准：
  - 高优先级任务插队后先执行；
  - `interruptible=true` 任务被打断后写 `TTS_INTERRUPTED` + 审计可回放；
  - 队列满时返回 `TTS_QUEUE_FULL` + 输出结构稳定；
  - T-VOICE-02 全部通过。
- 涉及文件路径：host_brain/voice/tts_queue.py（新增）；host_brain/audit/sqlite_ledger.py；host_brain/core/errors.py；tests/contract/test_tts_contract.py（新增）；tests/fixtures/voice/。
- 关联需求/测试：FR-VOICE-002；T-VOICE-02。

### TASK-0105 Barge-in 打断 + Edge 兜底小模型（M7 P2）
- 任务描述：实现 Barge-in 打断协议（ASR 检测到用户开口 → Edge 发 barge_in → Host 发 tts_interrupt）；实现 Edge 兜底小模型（Host 不可用时 draft_only 输出，默认卸载按需加载）。
- 输入：DevSpec §17.3.3~17.3.4；TASK-0103/0104 输出的 ASRHandler + TTSQueue；host_brain/audit/sqlite_ledger.py。
- 输出：
  - `host_brain/voice/barge_in.py`（新增）：BargeInHandler（接收 barge_in 事件 → 触发 tts_interrupt → 写 voice_event_log）；
  - `edge_client/fallback/fallback_model.py`（新增）：FallbackModel（draft_only 标记 + 按需加载 + 不可用快速失败）；
  - `host_brain/core/errors.py` 扩展：`FALLBACK_MODEL_UNAVAILABLE`、`FALLBACK_OUTPUT_DRAFT_ONLY` 错误码；
  - `tests/contract/test_barge_in_contract.py`（新增）：T-VOICE-03 验收测试；
  - `tests/contract/test_fallback_model_contract.py`（新增）：T-VOICE-04 验收测试；
  - `tests/fixtures/voice/` 扩展：barge_in_event.json / fallback_draft.json / fallback_unavailable.json。
- 验收标准：
  - Barge-in 触发后 TTS 立即打断 + 审计记录 `BARGE_IN_INTERRUPTED`；
  - 兜底输出标记 `draft_only=true`，不含裁决或执行事实宣告；
  - 兜底不可用时 `FALLBACK_MODEL_UNAVAILABLE` 快速失败；
  - T-VOICE-03/T-VOICE-04 全部通过。
- 涉及文件路径：host_brain/voice/barge_in.py（新增）；edge_client/fallback/fallback_model.py（新增）；host_brain/core/errors.py；tests/contract/test_barge_in_contract.py（新增）；tests/contract/test_fallback_model_contract.py（新增）；tests/fixtures/voice/。
- 关联需求/测试：FR-VOICE-003；FR-VOICE-004；T-VOICE-03；T-VOICE-04。

### TASK-0106 Edge 资源闸门：优先级矩阵 + 健康快照 + 抢占可观测（M7 P2）
- 任务描述：实现 Edge 资源闸门，按 P1（ASR）> P2（播放）> P3（TTS 合成）> P4（兜底 LLM）优先级矩阵管理资源，低优先级任务被抢占时写 `RESOURCE_GATE_PREEMPTED`，健康快照可观测。
- 输入：DevSpec §17.3.5；TASK-0103~0105 输出的 ASRHandler/TTSQueue/BargeInHandler/FallbackModel。
- 输出：
  - `edge_client/resource/resource_gate.py`（新增）：ResourceGate（优先级矩阵 + 抢占逻辑 + 健康快照）；
  - `edge_client/resource/__init__.py`（新增）：包初始化；
  - `host_brain/core/errors.py` 扩展：`RESOURCE_GATE_PREEMPTED` 错误码；
  - `tests/contract/test_resource_gate_contract.py`（新增）：T-VOICE-05 验收测试；
  - `tests/fixtures/voice/` 扩展：resource_gate_snapshot.json / preemption_event.json。
- 验收标准：
  - P4 任务在 P1 活跃时被抢占，写 `RESOURCE_GATE_PREEMPTED`；
  - ASR（P1）在压力下不被抢占；
  - 健康快照结构与 DevSpec §17.3.5 一致；
  - T-VOICE-05 全部通过。
- 涉及文件路径：edge_client/resource/resource_gate.py（新增）；edge_client/resource/__init__.py（新增）；host_brain/core/errors.py；tests/contract/test_resource_gate_contract.py（新增）；tests/fixtures/voice/。
- 关联需求/测试：FR-VOICE-005；T-VOICE-05。

---

## M8 运行时集成闭环（TASK-0107~0110）

> 对应 DevSpec §18；PRD M8（NFR-PERF-001、NFR-REL-001、FR-CORE-004）。
> 所有任务当前状态：DONE（2026-03-03）。

### TASK-0107 运行时脚本集成：start_host_side.ps1 接入 OBS/MCP 健康探测（M8 P0）
- 任务描述：扩展 `start_host_side.ps1`，在 `/health` 通过后额外探测 `/obs/metrics` 与 `MCPToolRegistry.health_check()`，将 OBS/MCP 子系统健康状态写入启动结果 JSON，不可用时输出 `startup_warnings` 但不阻断启动。
- 输入：DevSpec §18.1.1；scripts/runtime/start_host_side.ps1（现有）；host_brain/obs/routes.py；host_brain/mcp/tool_registry.py。
- 输出：
  - `scripts/runtime/start_host_side.ps1` 扩展：新增 `/obs/metrics` HTTP 探测逻辑 + `MCPToolRegistry` SQLite 可写性检查；
  - 启动结果 JSON 新增字段：`obs_status`（ok/degraded）、`mcp_registry_status`（ok/degraded）、`startup_warnings`（列表）；
  - `host_brain/core/errors.py` 扩展：`STARTUP_OBS_DEGRADED`、`STARTUP_MCP_DEGRADED` 错误码；
  - `tests/contract/test_startup_integration_contract.py`（新增）：T-RT-01 验收测试（启动结果 JSON 字段完整性 + OBS/MCP 状态可观测）；
  - `tests/fixtures/runtime/`（新增）：startup_ok.json / startup_obs_degraded.json / startup_mcp_degraded.json。
- 验收标准：
  - 正常启动：`obs_status=ok`、`mcp_registry_status=ok`、`startup_warnings=[]`；
  - OBS 不可用：`obs_status=degraded`、`startup_warnings` 含 `STARTUP_OBS_DEGRADED`，进程仍启动；
  - MCP SQLite 不可写：`mcp_registry_status=degraded`、`startup_warnings` 含 `STARTUP_MCP_DEGRADED`，进程仍启动；
  - T-RT-01 全部通过。
- 涉及文件路径：scripts/runtime/start_host_side.ps1；host_brain/core/errors.py；tests/contract/test_startup_integration_contract.py（新增）；tests/fixtures/runtime/。
- 关联需求/测试：NFR-REL-001；T-RT-01。

### TASK-0108 端到端 Smoke Test：冷启动闭环 + 执行链路 + 故障注入矩阵（M8 P0）
- 任务描述：实现三个端到端 Smoke 测试场景：① 冷启动最小闭环（无外部依赖）、② 执行链路（approval_token + undo）、③ 故障注入矩阵（Neo4j/Chroma/LLM/Edge 各自不可用时输出结构稳定）。
- 输入：DevSpec §18.2.1；host_brain/app/fastapi_app.py；host_brain/audit/sqlite_ledger.py；host_brain/governor/decision.py；host_brain/skills/loader.py；host_brain/lsp/vault.py。
- 输出：
  - `tests/e2e/test_cold_start_e2e.py`（新增）：T-E2E-01（冷启动最小闭环）；
  - `tests/e2e/test_exec_chain_e2e.py`（新增）：T-E2E-02（执行链路 + undo）；
  - `tests/e2e/test_fault_injection_e2e.py`（新增）：T-E2E-03（故障注入矩阵：Neo4j/Chroma/LLM/Edge）；
  - `tests/fixtures/e2e/`（新增）：cold_start_input.json / exec_chain_input.json / fault_injection_matrix.json。
- 验收标准：
  - T-E2E-01：无外部依赖下响应为合法 JSON，`quality.stage` 含降级标记，SQLite 有 intent_log + governor_log，无伪执行宣告；
  - T-E2E-02：执行链路完整（APPROVE→token→exec→manifest），undo 后文件恢复，vault_gc_log 不删有效 undo 窗口材料；
  - T-E2E-03：4 种故障注入下输出结构均稳定，`reason_code` 均可统计；
  - T-E2E-01~03 全部通过。
- 涉及文件路径：tests/e2e/test_cold_start_e2e.py（新增）；tests/e2e/test_exec_chain_e2e.py（新增）；tests/e2e/test_fault_injection_e2e.py（新增）；tests/fixtures/e2e/。
- 关联需求/测试：FR-CORE-004；NFR-REL-001；T-E2E-01~03。

### TASK-0109 性能基线采集：p50/p95 时延指标 + /obs/metrics 扩展（M8 P1）
- 任务描述：在 `MetricsCollector` 中新增 `latency_baseline` 聚合逻辑，从 `retrieval_event_log`/`governor_log`/`skill_exec_log` 计算 p50/p95，并在 `/obs/metrics` 响应中暴露；新增 `PERF_BASELINE_STALE` 错误码（采样数据超 24h 未更新）。
- 输入：DevSpec §18.3.1；host_brain/obs/metrics.py（现有 MetricsCollector）；host_brain/audit/sqlite_ledger.py（retrieval_event_log/governor_log/skill_exec_log）。
- 输出：
  - `host_brain/obs/metrics.py` 扩展：`collect_latency_baseline()` 方法，计算 p50/p95 并返回 `latency_baseline` 字典；
  - `host_brain/obs/routes.py` 扩展：`/obs/metrics` 响应中包含 `latency_baseline` 字段；
  - `host_brain/core/errors.py` 扩展：`PERF_BASELINE_STALE` 错误码；
  - `tests/contract/test_perf_baseline_contract.py`（新增）：T-PERF-01 验收测试（字段结构稳定 + 采样计数 > 0 + 时延值合理范围）；
  - `tests/fixtures/obs/` 扩展：metrics_with_latency.json。
- 验收标准：
  - `latency_baseline` 字段包含 retrieval_p50_ms/retrieval_p95_ms/governor_p50_ms/governor_p95_ms/skill_exec_p50_ms/skill_exec_p95_ms/sampled_at/sample_count；
  - `sample_count > 0` 时时延值为正整数；
  - `sample_count = 0` 时字段为 null 或含 `PERF_BASELINE_STALE` 标记；
  - T-PERF-01 全部通过。
- 涉及文件路径：host_brain/obs/metrics.py；host_brain/obs/routes.py；host_brain/core/errors.py；tests/contract/test_perf_baseline_contract.py（新增）；tests/fixtures/obs/。
- 关联需求/测试：NFR-PERF-001；T-PERF-01。

### TASK-0110 发布清单锁定：M8 release manifest（M8 P1）
- 任务描述：基于 `host_brain/release/release_manifest.py`，生成 M8 发布清单，锁定 git commit hash、所有 migration 文件 hash、测试通过数、性能基线快照、服务注册表快照，输出到 `release/manifests/m8_release.json`。
- 输入：DevSpec §18.4.1；host_brain/release/release_manifest.py（现有）；host_brain/audit/migrations/（所有 SQL 文件）；host_brain/bond/migrations/（所有 Cypher 文件）；host_brain/core/services.py（服务注册表）。
- 输出：
  - `host_brain/release/release_manifest.py` 扩展：新增 `generate_m8_manifest()` 方法，聚合上述字段；
  - `release/manifests/m8_release.json`（新增）：M8 发布清单文件；
  - `tests/contract/test_release_manifest_contract.py`（新增）：T-REL-01 验收测试（字段完整性 + hash 格式合法 + migration 文件数量与实际一致）；
  - `tests/fixtures/release/`（新增）：m8_manifest_expected.json。
- 验收标准：
  - `m8_release.json` 包含：git_commit_hash/migration_hashes（按文件名索引）/test_passed_count/latency_baseline_snapshot/service_registry_snapshot/generated_at；
  - `migration_hashes` 中文件数量与 `host_brain/audit/migrations/` + `host_brain/bond/migrations/` 实际文件数一致；
  - T-REL-01 全部通过。
- 涉及文件路径：host_brain/release/release_manifest.py；release/manifests/m8_release.json（新增）；tests/contract/test_release_manifest_contract.py（新增）；tests/fixtures/release/。
- 关联需求/测试：NFR-MAINT-001；T-REL-01。


---

## M9 World Canon 闭环 + 日记 Evolution 关联（TASK-0111~0115）

> 对应 DevSpec §19；PRD M9（FR-WCANON-001~003、FR-DIARY-002）。
> 所有任务当前状态：DONE（2026-03-03）。

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

---

## M9 收尾（TASK-0116）

> 对应 M9 文档/追踪/发布清单收尾工作。
> 所有任务当前状态：DONE（2026-03-03）。

### TASK-0116 M9 收尾：需求追踪补写 + 发布清单生成 + 启动脚本 canon_sync 钩子（M9 收尾）
- 任务描述：补齐 M9 的三项收尾工作：① requirements_trace.md 补写 R-85~R-89（M9 的 5 条需求追踪条目）；② 生成 M9 发布清单 release/manifests/m9_release.json（类比 M8 的 TASK-0110）；③ start_host_side.ps1 新增 canon_sync 健康探测钩子（/health/canon_sync，非阻断）。
- 输入：DevSpec §19；requirements_trace.md（现有，末尾为 R-84）；host_brain/release/release_manifest.py（现有 generate_m8_manifest）；scripts/runtime/start_host_side.ps1（现有 M8 探测逻辑）。
- 输出：
  - `docs/60_trace/requirements_trace.md` 扩展：新增 M9 章节标题 + R-85~R-89 五条追踪条目（DONE）；
  - `host_brain/release/release_manifest.py` 扩展：新增 `generate_m9_manifest()` 方法（含 world_canon_asset_hashes 字段）；
  - `release/manifests/m9_release.json`（新增）：M9 发布清单（17 migration 文件 + 3 个 World Canon 种子条目 hash + 1032 tests passed）；
  - `scripts/runtime/start_host_side.ps1` 扩展：新增 M9 canon_sync 健康探测块（/health/canon_sync，degraded 非阻断）+ 输出 JSON 新增 `canon_sync_status` 字段。
- 验收标准：
  - requirements_trace.md 包含 R-85~R-89，覆盖状态均为 DONE；
  - m9_release.json 包含：milestone=M9 / git_commit_hash / migration_hashes（17 文件）/ world_canon_asset_hashes（3 文件）/ test_passed_count=1032 / generated_at；
  - start_host_side.ps1 输出 JSON 含 canon_sync_status 字段；
  - start_edge.ps1 无需修改（ASR 热词注入为 M10-B 工作）。
- 涉及文件路径：docs/60_trace/requirements_trace.md；host_brain/release/release_manifest.py；release/manifests/m9_release.json（新增）；scripts/runtime/start_host_side.ps1。
- 关联需求/测试：FR-WCANON-001；FR-WCANON-002；FR-WCANON-003；FR-DIARY-002；NFR-MAINT-001。

---

## M10-A Canon 治理 API（TASK-0117~0120）

> 对应 DevSpec §20；PRD M10-A（P0）：Canon 提案审核 REST API。
> 将 M9 已落地的 `submit_proposal()` / `approve_proposal_with_content()` 暴露为可操作的 REST 端点。
> 所有任务当前状态：DONE（2026-03-03：1041 passed，Canon 治理 API 全量实现）。

### TASK-0117 WorldCanonStore.reject_proposal() 方法补充（M10-A 前置）
- 任务描述：在 `canon_world_store.py` 中补充 `reject_proposal()` 方法（status 改为 rejected，不修改 SSOT 文件），并在 `errors.py` 中新增 M10-A 错误码（WCANON_PROPOSAL_NOT_FOUND / WCANON_DOC_NOT_FOUND / WCANON_PROPOSAL_DUPLICATE / WCANON_PROPOSAL_STATUS_INVALID）。
- 输入：DevSpec §20.5；host_brain/memory/canon_world_store.py（现有，含 submit_proposal/approve_proposal_with_content）；host_brain/core/errors.py（现有）。
- 输出：
  - `host_brain/memory/canon_world_store.py` 扩展：`reject_proposal()` 方法（status=rejected，PermissionError 若非 pending）；
  - `host_brain/core/errors.py` 扩展：WCANON_PROPOSAL_NOT_FOUND / WCANON_DOC_NOT_FOUND / WCANON_PROPOSAL_DUPLICATE / WCANON_PROPOSAL_STATUS_INVALID 四个错误码；
  - `tests/contract/test_canon_proposal_reject_contract.py`（新增）：T-Canon-API-06 验收测试（reject 成功 + 非 pending 拒绝）。
- 验收标准：
  - reject_proposal() 对 pending 提案成功，status=rejected，SSOT 文件不变；
  - reject_proposal() 对非 pending 提案抛出 PermissionError（WCANON_PROPOSAL_STATUS_INVALID）；
  - reject_proposal() 对不存在的 proposal_id 抛出 KeyError（WCANON_PROPOSAL_NOT_FOUND）；
  - T-Canon-API-06 全部通过。
- 涉及文件路径：host_brain/memory/canon_world_store.py；host_brain/core/errors.py；tests/contract/test_canon_proposal_reject_contract.py（新增）。
- 关联需求/测试：FR-WCANON-002；T-Canon-API-06。

### TASK-0118 canon_routes.py：REST 路由实现（GET/POST /canon/proposals + GET /canon/documents）
- 任务描述：新建 `host_brain/app/canon_routes.py`，实现 DevSpec §20.2.1 的 7 个端点（列出提案、提交提案、查询提案、审核通过、拒绝提案、列出文档、查询文档），并在 `fastapi_app.py` 中注册路由。
- 输入：DevSpec §20.2~20.4；host_brain/app/fastapi_app.py（现有路由注册方式）；host_brain/memory/canon_world_store.py（TASK-0117 扩展后）；host_brain/audit/sqlite_ledger.py（现有 get_proposal/list_proposals 等方法）。
- 输出：
  - `host_brain/app/canon_routes.py`（新增）：7 个端点实现，含 Pydantic 请求/响应模型（CanonProposalSubmitRequest / CanonProposalApproveRequest / CanonProposalRejectRequest / CanonProposalResponse / CanonDocumentResponse / CanonProposalListResponse）；
  - `host_brain/app/fastapi_app.py` 扩展：`app.include_router(canon_router)` 注册；`get_canon_store()` 依赖注入函数；
  - 每个写操作生成 `request_id` 并写入 `proposer_node_id`（格式：`api.canon_proposals::{request_id}`）。
- 验收标准：
  - GET /canon/proposals 返回分页列表，status 过滤正确（T-Canon-API-07）；
  - POST /canon/proposals 提交成功，status=pending，proposal_id 可查询（T-Canon-API-01）；
  - POST /canon/proposals 重复 proposal_id 返回 409 + WCANON_PROPOSAL_DUPLICATE（T-Canon-API-02）；
  - POST /canon/proposals body 含完成性表述返回 400 + WCANON_PROPOSAL_INVALID（T-Canon-API-03）；
  - GET /canon/documents 返回当前 SSOT 快照（T-Canon-API-08）；
  - 所有端点响应结构稳定（含 request_id + timestamp）。
- 涉及文件路径：host_brain/app/canon_routes.py（新增）；host_brain/app/fastapi_app.py；tests/contract/test_canon_api_contract.py（新增）；tests/fixtures/canon/。
- 关联需求/测试：FR-WCANON-001；FR-WCANON-002；T-Canon-API-01~03；T-Canon-API-07~08。

### TASK-0119 canon_routes.py：approve/reject 端点 + 审计串联（M10-A P0 核心）
- 任务描述：实现 `POST /canon/proposals/{proposal_id}/approve` 和 `POST /canon/proposals/{proposal_id}/reject` 端点，确保审计串联（approve 写 canon_change_log，reject 写 system_event_log），并补充对应的 contract 测试。
- 输入：DevSpec §20.4；host_brain/app/canon_routes.py（TASK-0118 实现后）；host_brain/memory/canon_world_store.py（含 approve_proposal_with_content / reject_proposal）；host_brain/audit/sqlite_ledger.py（含 log_system_event）。
- 输出：
  - `host_brain/app/canon_routes.py` 扩展：approve 端点（调用 approve_proposal_with_content，写 canon_change_log）；reject 端点（调用 reject_proposal，写 system_event_log，event_type=WCANON_PROPOSAL_REJECTED）；
  - `tests/contract/test_canon_api_approve_reject_contract.py`（新增）：T-Canon-API-04（approve 成功 + SSOT 更新 + change_log 可回放）+ T-Canon-API-05（非 pending 提案 approve 返回 422）。
- 验收标准：
  - approve 成功：SSOT 文件版本 +1，canon_change_log 含变更前后 hash，response.status=APPLIED（T-Canon-API-04）；
  - approve 非 pending：返回 422 + WCANON_PROPOSAL_STATUS_INVALID（T-Canon-API-05）；
  - reject 成功：status=rejected，SSOT 文件不变，system_event_log 含 WCANON_PROPOSAL_REJECTED（T-Canon-API-06 扩展）；
  - 所有写操作 proposer_node_id 含 request_id（审计串联可验证）。
- 涉及文件路径：host_brain/app/canon_routes.py；host_brain/audit/sqlite_ledger.py；tests/contract/test_canon_api_approve_reject_contract.py（新增）；tests/fixtures/canon/。
- 关联需求/测试：FR-WCANON-002；T-Canon-API-04；T-Canon-API-05；T-Canon-API-06。

### TASK-0120 M10-A 发布清单生成 + requirements_trace.md 补写 R-90~R-91
- 任务描述：补齐 M10-A 的两项收尾工作：① requirements_trace.md 补写 R-90~R-91（M10-A 的 2 条需求追踪条目）；② 在 release_manifest.py 中新增 generate_m10a_manifest()，生成 release/manifests/m10a_release.json。
- 输入：DevSpec §20；requirements_trace.md（现有，末尾为 R-89）；host_brain/release/release_manifest.py（现有 generate_m9_manifest）。
- 输出：
  - `docs/60_trace/requirements_trace.md` 扩展：新增 M10-A 章节标题 + R-90（FR-WCANON-002 Canon 治理 API）+ R-91（NFR-MAINT-001 M10-A 发布清单）；
  - `host_brain/release/release_manifest.py` 扩展：`generate_m10a_manifest()` 方法；
  - `release/manifests/m10a_release.json`（新增）：M10-A 发布清单。
- 验收标准：
  - requirements_trace.md 包含 R-90~R-91，覆盖状态均为 DONE；
  - m10a_release.json 包含：milestone=M10-A / git_commit_hash / migration_hashes / test_passed_count / generated_at；
  - canon_routes.py 端点在 /docs 可见（FastAPI 自动文档）。
- 涉及文件路径：docs/60_trace/requirements_trace.md；host_brain/release/release_manifest.py；release/manifests/m10a_release.json（新增）。
- 关联需求/测试：FR-WCANON-001；FR-WCANON-002；NFR-MAINT-001；T-Canon-API-01~08。


---

## M10-A 方向 A — P1 风险修复（2026-03-03）

### TASK-0121-P1 P1 风险修复：proposal_id UNIQUE 约束 + 循环导入消除 + rollback 机制
- 状态：DONE（2026-03-03：1046 passed，P1 三项修复全量落地）
- 任务描述：修复 risk_registry.md 中 M10-A 新增的三项 P1 风险：R118-3（proposal_id 无 UNIQUE 约束）、R118-2（canon_routes 循环导入）、R114-1（Evolution approve 无回滚机制）。
- 输入：risk_registry.md R118-3/R118-2/R114-1；host_brain/audit/sqlite_ledger.py；host_brain/app/canon_routes.py；host_brain/app/fastapi_app.py；host_brain/memory/canon_world_store.py。
- 输出：
  - `host_brain/audit/migrations/0017_canon_proposal_log_unique.sql`（新增）：UNIQUE INDEX 文档；
  - `host_brain/audit/sqlite_ledger.py` 扩展：`_ensure_schema` 追加 `idx_canon_proposal_id_unique` + `idx_canon_change_id_unique`；`log_canon_proposal` 改为 `INSERT OR IGNORE`（幂等写入）；
  - `host_brain/app/canon_routes.py` 重构：`_get_ledger_from_app` 改为外部注入 provider（`set_ledger_provider`），消除延迟导入循环引用；新增 `POST /canon/proposals/{id}/rollback` 端点 + `CanonProposalRollbackRequest/Response` 模型；
  - `host_brain/app/fastapi_app.py` 扩展：导入 `set_ledger_provider`，在 `get_ledger` 定义后调用 `_set_canon_ledger_provider(get_ledger)`；
  - `host_brain/memory/canon_world_store.py` 扩展：新增 `rollback_proposal()` 方法（APPLIED→ROLLED_BACK，写 canon_change_log，base_version=0 时删除 SSOT 文件）；
  - `tests/contract/test_canon_p1_fixes.py`（新增）：T-Canon-P1-01~05（5 个测试）。
- 验收标准：
  - 相同 proposal_id 重复写入后数据库只有一条记录（T-Canon-P1-01）；
  - 直接 import canon_routes 不触发 ImportError（T-Canon-P1-02）；
  - set_ledger_provider 注入后 _get_ledger_from_app 返回注入实例（T-Canon-P1-03）；
  - APPLIED 提案可回滚，SSOT 文件删除/降版，status=ROLLED_BACK（T-Canon-P1-04）；
  - pending/rejected 提案回滚抛 PermissionError（T-Canon-P1-05）；
  - 全量测试 1046 passed，0 failed。
- 涉及文件路径：host_brain/audit/migrations/0017_canon_proposal_log_unique.sql（新增）；host_brain/audit/sqlite_ledger.py；host_brain/app/canon_routes.py；host_brain/app/fastapi_app.py；host_brain/memory/canon_world_store.py；tests/contract/test_canon_p1_fixes.py（新增）。
- 关联需求/测试：risk_registry.md R118-3/R118-2/R114-1；T-Canon-P1-01~05。

---

## M10-B — ASR 热词注入 World Canon 关键词（DONE，2026-03-03）

### TASK-0122 canon_world_store.py：extract_hotwords() 方法
- 状态：DONE（2026-03-03）
- 任务描述：在 WorldCanonStore 中新增 `extract_hotwords()` 方法，从 SSOT 文档中提取人名/地名/专有名词作为 ASR 热词列表。
- 输入：DevSpec §20（M10-B 候选）；host_brain/memory/canon_world_store.py；host_brain/canon/world/*.json（SSOT 文件）。
- 输出：
  - `host_brain/memory/canon_world_store.py` 扩展：`extract_hotwords(max_words: int = 200) -> list[str]` 方法，从所有 active 文档的 title/tags 中提取热词，去重排序；
  - `tests/unit/test_canon_hotwords.py`（新增）：T-Canon-HW-01~03。
- 验收标准：
  - extract_hotwords 返回 list[str]，无重复，按字母序排序；
  - max_words 参数生效（截断超出部分）；
  - 空 SSOT 目录返回空列表。
- 涉及文件路径：host_brain/memory/canon_world_store.py；tests/unit/test_canon_hotwords.py（新增）。
- 关联需求/测试：FR-WCANON-001；T-Canon-HW-01~03。

### TASK-0123 canon_routes.py：GET /canon/hotwords 端点
- 状态：DONE（2026-03-03）
- 任务描述：在 canon_routes.py 中新增 `GET /canon/hotwords` 端点，返回当前 World Canon 热词列表（供 Edge ASR 模块拉取）。
- 输入：DevSpec §20（M10-B）；host_brain/app/canon_routes.py；WorldCanonStore.extract_hotwords()（TASK-0122 实现后）。
- 输出：
  - `host_brain/app/canon_routes.py` 扩展：`GET /canon/hotwords` 端点，响应结构 `{hotwords: list[str], total: int, request_id: str, timestamp: int}`；
  - `tests/contract/test_canon_hotwords_api.py`（新增）：T-Canon-HW-API-01~02。
- 验收标准：
  - GET /canon/hotwords 返回 hotwords 列表，total 与列表长度一致；
  - max_words 查询参数生效（默认 200）。
- 涉及文件路径：host_brain/app/canon_routes.py；tests/contract/test_canon_hotwords_api.py（新增）。
- 关联需求/测试：FR-WCANON-001；T-Canon-HW-API-01~02。

### TASK-0124 edge_client/senses/asr.py：热词注入接口扩展
- 状态：DONE（2026-03-03）
- 任务描述：在 Edge ASR 模块中扩展热词注入接口，支持从 Host `GET /canon/hotwords` 拉取热词并注入到 ASR 引擎。
- 输入：DevSpec §20（M10-B）；edge_client/senses/asr.py（现有）；GET /canon/hotwords 端点（TASK-0123 实现后）。
- 输出：
  - `edge_client/senses/asr.py` 扩展：`inject_canon_hotwords(host_url: str, max_words: int = 200) -> int` 方法，拉取热词并注入，返回注入词数；
  - `tests/unit/test_asr_hotword_injection.py`（新增）：T-ASR-HW-01~03（mock HTTP 请求）。
- 验收标准：
  - inject_canon_hotwords 成功时返回注入词数 > 0；
  - Host 不可达时降级（返回 0，不抛异常）；
  - 热词列表为空时返回 0。
- 涉及文件路径：edge_client/senses/asr.py；tests/unit/test_asr_hotword_injection.py（新增）。
- 关联需求/测试：T-ASR-HW-01~03。

### TASK-0125 M10-B 发布清单 + requirements_trace.md 补写 R-92~R-94
- 状态：DONE（2026-03-03）
- 任务描述：补齐 M10-B 的收尾工作：① requirements_trace.md 补写 R-92~R-94；② 生成 release/manifests/m10b_release.json；③ release_manifest.py 追加 generate_m10b_manifest()。
- 输入：DevSpec §20（M10-B）；requirements_trace.md（末尾为 R-91）；host_brain/release/release_manifest.py。
- 输出：
  - `docs/60_trace/requirements_trace.md` 扩展：R-92（FR-WCANON-001 extract_hotwords）+ R-93（NFR-PERF-001 热词拉取延迟 < 200ms）+ R-94（FR-ASR-001 inject_canon_hotwords）；
  - `release/manifests/m10b_release.json`（新增）：M10-B 发布清单，test_passed_count=1054；
  - `host_brain/release/release_manifest.py` 扩展：`generate_m10b_manifest()` 方法。
- 验收标准：
  - requirements_trace.md 包含 R-92~R-94，覆盖状态均为 DONE；
  - m10b_release.json 包含：milestone=M10-B / git_commit_hash / migration_hashes / asr_hash / test_passed_count=1054 / generated_at；
  - 全量测试 1054 passed，0 failed。
- 涉及文件路径：docs/60_trace/requirements_trace.md；release/manifests/m10b_release.json（新增）；host_brain/release/release_manifest.py。
- 关联需求/测试：FR-WCANON-001；NFR-PERF-001；FR-ASR-001；T-Canon-HW-01~03；T-Canon-HW-API-01~02；T-ASR-HW-01~03。

---

## M10-C — 记忆蒸馏系统（DONE，2026-03-03）

### TASK-0126 memory/distillation.py：MemoryDistillationEngine
- 状态：DONE（2026-03-03）
- 任务描述：新建 `host_brain/memory/distillation.py`，实现 `MemoryDistillationEngine` 类，从情节记忆（EpisodicStore）中蒸馏语义记忆（SemanticMemory），写入 SQLite `semantic_memory` 表。
- 输入：DevSpec §20（M10-C 候选）；host_brain/memory/episodic.py；host_brain/audit/sqlite_ledger.py。
- 输出：
  - `host_brain/memory/distillation.py`（新增）：`MemoryDistillationEngine` 类，含 `distill_top_k()` 方法（召回 Top-K 情节记忆 → 合并摘要 → 幂等写入 semantic_memory 表 → 写 system_event_log 审计）；`SemanticMemoryRecord` / `DistillResult` 数据类；`_compute_source_hash()` / `_merge_texts()` / `_compute_confidence()` 辅助函数。
- 验收标准：
  - distill_top_k 正常蒸馏返回 semantic_id，写入 semantic_memory 表（T-Distill-01）；
  - 相同 source_memory_ids 重复蒸馏跳过（skipped=True，T-Distill-02）；
  - 空 EpisodicStore 返回 success=False（T-Distill-03）。
- 涉及文件路径：host_brain/memory/distillation.py（新增）。
- 关联需求/测试：T-Distill-01~03。

### TASK-0127 sqlite_ledger.py：semantic_memory 表 + CRUD 方法
- 状态：DONE（2026-03-03）
- 任务描述：在 `sqlite_ledger.py` 中新增 `semantic_memory` 表定义（`_ensure_schema`）和三个 CRUD 方法。
- 输入：DevSpec §20（M10-C）；host_brain/audit/sqlite_ledger.py。
- 输出：
  - `host_brain/audit/sqlite_ledger.py` 扩展：`_ensure_schema` 追加 `semantic_memory` 表（含 semantic_id UNIQUE / source_hash UNIQUE / ts_ms INDEX）；新增 `log_semantic_memory()` / `get_semantic_memory_by_source_hash()` / `list_semantic_memories()` / `_row_to_semantic_memory()` 方法。
- 验收标准：
  - log_semantic_memory 幂等写入（INSERT OR IGNORE）；
  - get_semantic_memory_by_source_hash 按 source_hash 查询；
  - list_semantic_memories 分页查询正确（T-Distill-04）。
- 涉及文件路径：host_brain/audit/sqlite_ledger.py。
- 关联需求/测试：T-Distill-04。

### TASK-0128 context_pack.py：semantic_slot 可选分槽
- 状态：DONE（2026-03-03）
- 任务描述：在 `context_pack.py` 中新增可选 `semantic_slot` 分槽支持，包含验证函数和构建辅助函数。
- 输入：DevSpec §20（M10-C）；host_brain/core/context_pack.py。
- 输出：
  - `host_brain/core/context_pack.py` 扩展：新增 `OPTIONAL_SLOTS` 字典（含 `semantic_slot: list`）；`validate_context_pack` 扩展：未知槽位检查同时排除 OPTIONAL_SLOTS，存在 semantic_slot 时调用 `_validate_semantic_slot()`；新增 `_validate_semantic_slot()` 函数（校验 semantic_id / summary 必填字段）；新增 `build_semantic_slot()` 辅助函数（从 ledger 构建 semantic_slot 列表）。
- 验收标准：
  - 合法 semantic_slot 通过验证（T-Distill-05）；
  - 缺少 semantic_id/summary 时报 CONTEXT_PACK_TYPE_INVALID（T-Distill-05）；
  - 不含 semantic_slot 的 context_pack 仍通过验证（T-Distill-05）；
  - build_semantic_slot 返回格式正确的列表（T-Distill-06）；
  - 全量测试 1062 passed，0 failed（无回归）。
- 涉及文件路径：host_brain/core/context_pack.py。
- 关联需求/测试：T-Distill-05~06。

### TASK-0129 M10-C 测试 + 发布清单
- 状态：DONE（2026-03-03）
- 任务描述：补齐 M10-C 的测试文件和发布清单。
- 输入：DevSpec §20（M10-C）；host_brain/release/release_manifest.py。
- 输出：
  - `tests/unit/test_memory_distillation.py`（新增）：T-Distill-01~06（8 个测试）；
  - `release/manifests/m10c_release.json`（新增）：M10-C 发布清单，test_passed_count=1062；
  - `host_brain/release/release_manifest.py` 扩展：`generate_m10c_manifest()` 方法（锁定 distillation.py / context_pack.py / sqlite_ledger.py hash）；
  - `docs/60_trace/requirements_trace.md` 扩展：R-95~R-97（M10-C 语义记忆蒸馏相关需求追踪）。
- 验收标准：
  - T-Distill-01~06 全部通过；
  - m10c_release.json 包含：milestone=M10-C / distillation_hash / context_pack_hash / sqlite_ledger_hash / test_passed_count=1062；
  - 全量测试 1062 passed，0 failed。
- 涉及文件路径：tests/unit/test_memory_distillation.py（新增）；release/manifests/m10c_release.json（新增）；host_brain/release/release_manifest.py；docs/60_trace/requirements_trace.md。
- 关联需求/测试：T-Distill-01~06。

---

## M10-D — P1/P2 收口与发布（DONE，2026-03-03）

### TASK-0130 F-41 修复：distill_top_k() 并发保护（蒸馏级别锁）
- 状态：DONE（2026-03-03）
- 任务描述：在 `memory/distillation.py` 为 `distill_top_k()` 增加线程并发保护，避免并发蒸馏同一 source 集合时产生 source_hash 竞态。
- 输入：risk_registry.md（R-MC-02）；host_brain/memory/distillation.py；tests/unit/test_memory_distillation.py。
- 输出：
  - `host_brain/memory/distillation.py` 扩展：`MemoryDistillationEngine` 增加蒸馏级别互斥锁（threading.Lock）；
  - `distill_top_k()` 关键区增加“锁内二次幂等检查 + 写入后回读确认”；
  - `tests/unit/test_memory_distillation.py` 扩展：新增并发回归测试（多线程同源蒸馏）。
- 验收标准：
  - 并发场景下同一 source_hash 最终仅落库一次；
  - 不出现重复 semantic_memory 记录；
  - 并发测试稳定通过。
- 涉及文件路径：host_brain/memory/distillation.py；tests/unit/test_memory_distillation.py。
- 关联需求/测试：FR-MEM-001；T-Distill-07（并发补充）。

### TASK-0131 F-06 修复：_table_has_column 表结构缓存（线程安全）
- 状态：DONE（2026-03-03）
- 任务描述：为 `SQLiteLedger._table_has_column()` 增加线程安全的列结构缓存，降低重复 PRAGMA 查询开销。
- 输入：risk_registry.md（R119-3）；host_brain/audit/sqlite_ledger.py；tests/contract/test_quality_contract.py。
- 输出：
  - `host_brain/audit/sqlite_ledger.py` 扩展：新增 `_column_cache_lock` 与双检锁缓存填充；
  - `_table_has_column()` 命中缓存时不再重复查询 `PRAGMA table_info`；
  - `tests/contract/test_quality_contract.py` 扩展：并发缓存行为断言测试。
- 验收标准：
  - 首次调用填充缓存，后续命中缓存；
  - 并发调用场景仅发生一次 PRAGMA 查询；
  - 质量契约测试通过。
- 涉及文件路径：host_brain/audit/sqlite_ledger.py；tests/contract/test_quality_contract.py。
- 关联需求/测试：NFR-PERF-001；T-Quality-01（F-06 子项）。

### TASK-0132 F-35/F-36 收口：approve/reject 审计原子性增强
- 状态：DONE（2026-03-03）
- 任务描述：收口 `canon_routes.py` 中 approve/reject 的非原子风险，减少非事务窗口并将审计写入失败升级为错误返回。
- 输入：risk_registry.md（R119-1/R119-2）；host_brain/app/canon_routes.py。
- 输出：
  - `approve_proposal` 去除“二次 DB 读取依赖”，以前置读取结果构造响应；
  - `reject_proposal` 中 `log_system_event` 失败从 warning 升级为 error + HTTP 500；
  - 响应头补充 `X-Reason-Code: CANON_SYNC_FAILED`（审计失败可观测）。
- 验收标准：
  - approve 路径不再存在额外二次读取窗口；
  - reject 审计写入失败不再静默吞掉；
  - Canon API 契约测试通过。
- 涉及文件路径：host_brain/app/canon_routes.py；tests/contract/test_canon_api_contract.py。
- 关联需求/测试：FR-WCANON-002；T-Canon-API-04~06。

### TASK-0133 F-37 修复：M10-A 清单补锁核心文件 hash
- 状态：DONE（2026-03-03）
- 任务描述：扩展 `m10a_release.json` 锁定范围，补齐 `canon_world_store.py` 与 `sqlite_ledger.py` hash。
- 输入：risk_registry.md（R120-1）；host_brain/release/release_manifest.py；release/manifests/m10a_release.json。
- 输出：
  - `generate_m10a_manifest()` 增加 `canon_world_store_hash`、`sqlite_ledger_hash` 字段；
  - `release/manifests/m10a_release.json` 写入新增 hash 字段并更新值。
- 验收标准：
  - m10a 清单包含新增锁定字段；
  - 字段值为合法 sha256 摘要；
  - 发布契约检查通过。
- 涉及文件路径：host_brain/release/release_manifest.py；release/manifests/m10a_release.json。
- 关联需求/测试：NFR-MAINT-001；T-REL-01（扩展口径）。

### TASK-0134 F-39 修复：source_intent_id 端到端完整性校验
- 状态：DONE（2026-03-03）
- 任务描述：补齐 diary evolution proposal 的 `source_intent_id` 从提交到落库链路，防止跨表关联断链。
- 输入：risk_registry.md（R115-1）；host_brain/memory/canon_world_store.py；host_brain/audit/sqlite_ledger.py；host_brain/skills/impl/diary_writer.py。
- 输出：
  - `canon_proposal_log` schema 增加 `source_intent_id`；
  - `submit_proposal(..., source_intent_id=...)` 参数贯通；
  - `diary_writer` 在提案提交时传入 diary intent_id；
  - 迁移脚本与契约测试同步更新。
- 验收标准：
  - proposal 记录可回溯到源 intent_id；
  - diary evolution 契约测试同时验证返回值与 DB 落库字段；
  - 无 source_intent_id 时保持向后兼容。
- 涉及文件路径：host_brain/audit/sqlite_ledger.py；host_brain/audit/migrations/0002_canon_schema.sql；host_brain/audit/migrations/0016_canon_proposal_log.sql；host_brain/memory/canon_world_store.py；host_brain/skills/impl/diary_writer.py；tests/contract/test_diary_evolution_contract.py。
- 关联需求/测试：FR-DIARY-002；T-Diary-03；T-Diary-04。

### TASK-0135 F-12 收口：requirements 可选 Redis 依赖说明
- 状态：DONE（2026-03-03）
- 任务描述：在 `requirements.txt` 明确 Redis 为可选依赖，仅在启用 Redis rate-limit backend 时安装。
- 输入：risk_registry.md（R121-1）；requirements.txt；tests/contract/test_quality_contract.py。
- 输出：
  - `requirements.txt` 增加 Optional Dependencies 注释块（redis 可选说明）；
  - 质量契约补充断言（要求存在“redis + 可选”说明）。
- 验收标准：
  - requirements 文本含 redis 与 optional/可选标识；
  - 不强制默认安装 redis；
  - 对应质量契约测试通过。
- 涉及文件路径：requirements.txt；tests/contract/test_quality_contract.py。
- 关联需求/测试：NFR-MAINT-001；T-Quality-01（F-12 子项）。

### TASK-0136 M10-D 收口：追踪补写 + 第六版审查报告 + 发布清单
- 状态：DONE（2026-03-03）
- 任务描述：完成 M10-D 文档与发布链路收口：补写 `R-98~R-104`，生成第六版审查报告，生成 `m10d_release.json`。
- 输入：docs/60_trace/requirements_trace.md；docs/40_agents/full_code_review_report_2026-03-03-v5.md；host_brain/release/release_manifest.py。
- 输出：
  - `docs/60_trace/requirements_trace.md` 扩展：R-98~R-104；
  - `docs/40_agents/full_code_review_report_2026-03-03-v6.md`（新增）；
  - `host_brain/release/release_manifest.py` 扩展：`generate_m10d_manifest()`；
  - `release/manifests/m10d_release.json`（新增）。
- 验收标准：
  - 需求追踪新增条目完整，状态为 DONE；
  - 第六版报告更新剩余 P1/P2 发现与修复进展；
  - m10d_release.json 包含 milestone/git hash/migration hashes/核心文件 hash/test_passed_count/generated_at。
- 涉及文件路径：docs/60_trace/requirements_trace.md；docs/40_agents/full_code_review_report_2026-03-03-v6.md；host_brain/release/release_manifest.py；release/manifests/m10d_release.json。
- 关联需求/测试：NFR-MAINT-001；T-REL-01（发布清单口径扩展）。

### TASK-0137 F-04 跟进：Redis 后端真实集成测试落地
- 状态：BLOCKED_ENV（2026-03-04，暂缓——单机 SQLite 部署无需 Redis；多实例部署前执行）
- 任务描述：为 `RedisRateLimitStore` 增加真实 Redis 环境集成测试，验证共享计数与 checker 行为，消除“仅降级路径覆盖”的验证缺口。
- 输入：risk_registry.md（R39-1）；host_brain/governor/rate_limit_store.py；tests/contract/test_governor_rate_limit_contract.py。
- 输出：
  - `tests/integration/test_rate_limit_redis_integration.py`（新增）：
    - 真实 Redis 后端 `check/check_only/reset/ttl` 验证；
    - 双实例共享计数一致性验证；
    - 通过 `NASTUME_TEST_REDIS_URL` 环境变量启用，缺省自动 skip（不影响默认 CI）。
- 验收标准：
  - 配置 `NASTUME_TEST_REDIS_URL` 且 Redis 可达时，集成测试通过；
  - 未配置或 Redis 不可达时，测试稳定 skip；
  - 不引入默认测试链路不稳定性。
- 涉及文件路径：tests/integration/test_rate_limit_redis_integration.py（新增）。
- 关联需求/测试：FR-GOV-001；T-Gov-R4-Redis-01（集成补充）。

### TASK-0138 F-05 跟进：WAL/NFS 部署文档显式约束
- 状态：DONE（2026-03-03）
- 任务描述：在部署文档中显式补充 SQLite WAL 与 NFS 不兼容风险及验收口径，避免生产部署踩坑。
- 输入：risk_registry.md（R36-1）；docs/50_project/ExternalServices_Config.md；docs/50_project/ENV_EXTERNAL_SERVICES_CHECKLIST.md。
- 输出：
  - `ExternalServices_Config.md` 新增 SQLite WAL 存储介质要求（本地磁盘优先，NFS/SMB 禁用建议）；
  - `ENV_EXTERNAL_SERVICES_CHECKLIST.md` 新增部署前强校验条目（journal_mode/WAL 与 `AUDIT_WAL_FAILED` 告警口径）。
- 验收标准：
  - 部署文档明确“Ledger 主库禁止 NFS/网络文件系统”；
  - 明确 WAL 失败降级语义与运维检查方法；
  - 与 risk_registry 的 R36-1 对齐。
- 涉及文件路径：docs/50_project/ExternalServices_Config.md；docs/50_project/ENV_EXTERNAL_SERVICES_CHECKLIST.md。
- 关联需求/测试：NFR-REL-001；NFR-MAINT-001。

### TASK-0139 F-01/F-02 收口：Sandbox 容器硬隔离 + 跨平台超时一致化
- 状态：DONE（2026-03-03）
- 任务描述：收口 `airlock/sandbox.py` 在部署环境的两项 P1 风险：生产环境容器硬隔离约束（F-01）与 Windows/Linux 超时终止语义一致化（F-02）。
- 输入：risk_registry.md（F-01/F-02）；host_brain/airlock/sandbox.py；tests/contract/test_sandbox_contract.py；tests/contract/test_sandbox_docker_contract.py。
- 输出：
  - `host_brain/airlock/sandbox.py` 扩展：`SandboxConfig.require_container`、`isolation_backend=auto`、生产环境容器必选判定；
  - 容器必选且不可用时返回 `UNAVAILABLE + SANDBOX_CONTAINER_UNAVAILABLE`（禁止降级到 subprocess）；
  - subprocess 路径统一采用 `Popen.communicate(timeout=...)` + 平台感知终止（Windows `taskkill /T /F`，Linux/Unix `killpg`）；
  - `resource_usage` 补充 `timeout_enforcement/platform/isolation_backend` 诊断字段。
- 验收标准：
  - 生产环境或 `require_container=True` 时，不满足容器隔离必须硬失败；
  - 超时路径在 Windows/Linux 返回一致语义（TIMEOUT + SANDBOX_TIMEOUT）；
  - T-Sandbox-01 与 T-Sandbox-Docker-01 回归通过（含新增断言）。
- 涉及文件路径：host_brain/airlock/sandbox.py；tests/contract/test_sandbox_contract.py；tests/contract/test_sandbox_docker_contract.py。
- 关联需求/测试：FR-AIRLOCK-002；T-Sandbox-01；T-Sandbox-Docker-01。

### TASK-0140 F-11 收口：WeChat→Edge 真实 RPC 端到端验证
- 状态：DONE（2026-03-03）
- 任务描述：补齐 F-11 验证缺口：E2E 测试不再直接写 Ledger 模拟 Edge，而是经 Host `POST /v1/edge/events` 真实 RPC 入口写入语音事件。
- 输入：risk_registry.md（F-11）；host_brain/app/fastapi_app.py；edge_client/rpc/event_reporter.py；tests/e2e fixtures。
- 输出：
  - `tests/e2e/test_wechat_vts_voice_rpc_e2e.py`（新增）：覆盖 WeChat 入站 → Governor APPROVE → Skill 执行 → EdgeEventReporter 通过 HTTP 上报 `ASR_CHUNK/TTS_PLAY`；
  - E2E 断言补充：`voice_event_log` 真实落库 + `trace_request` 链路完整；
  - 测试侧 Ledger 连接调整为 `check_same_thread=False`，保证 TestClient 跨线程写库稳定。
- 验收标准：
  - 事件上报通过真实 `/v1/edge/events` 入口（非直接调用 Ledger）；
  - `ASR_CHUNK` 与 `TTS_PLAY` 均可在审计表中回放；
  - 聚焦 E2E+RPC+Sandbox + manifest 回归通过（102 passed）。
- 涉及文件路径：tests/e2e/test_wechat_vts_voice_rpc_e2e.py（新增）；host_brain/airlock/sandbox.py；tests/contract/test_sandbox_contract.py；tests/contract/test_sandbox_docker_contract.py。
- 关联需求/测试：FR-CHANNEL-001；FR-VOICE-001；T-E2E-RPC-01。

---

## M10-E：_CANON_HOTWORDS_CACHE 增加 TTL 自动失效（F-42）

### TASK-0141 asr.py：_HotwordsCache 类实现（TTL 自动失效）
- 状态：DONE（2026-03-04）
- 任务描述：在 `edge_client/senses/asr.py` 中新增 `_HotwordsCache` 类，为模块级热词缓存增加 TTL 自动失效机制，避免 SSOT 更新后缓存长期不刷新。
- 输入：risk_registry.md（F-42）；edge_client/senses/asr.py（M10-B 实现）。
- 输出：
  - `_HotwordsCache` 类：`set()/get()/clear()/is_valid()/age_s()` 方法，`threading.Lock` 保护，TTL 默认 300s；
  - `_CANON_HOTWORDS_CACHE = _HotwordsCache(ttl_s=300.0)` 模块级实例；
  - `inject_canon_hotwords()` 更新：注入后调用 `_CANON_HOTWORDS_CACHE.set(hotwords)` 并记录 TTL 日志；
  - `get_canon_hotwords()` 更新：调用 `_CANON_HOTWORDS_CACHE.get()` 返回 TTL 内副本。
- 验收标准：
  - TTL 未到期时 `get()` 返回完整热词列表；
  - TTL 到期后 `get()` 返回空列表；
  - `clear()` 立即使缓存失效；
  - 多线程并发 `set()` 不引发竞态。
- 涉及文件路径：edge_client/senses/asr.py。
- 关联需求/测试：FR-ASR-001；NFR-PERF-001；T-HW-TTL-01~05。

### TASK-0142 tests：test_hotwords_cache_ttl.py（T-HW-TTL-01~05）
- 状态：DONE（2026-03-04）
- 任务描述：新增 `_HotwordsCache` TTL 专项测试，覆盖 TTL 有效期内/到期后行为、`is_valid()` 状态转换、`clear()` 立即失效、并发写入安全。
- 输入：edge_client/senses/asr.py（TASK-0141 实现后）。
- 输出：
  - `tests/unit/test_hotwords_cache_ttl.py`（新增）：T-HW-TTL-01~05（5 tests）；
  - 使用 `unittest.mock.patch("time.monotonic")` 模拟时间推进，无需真实等待。
- 验收标准：
  - 5 个测试用例全部通过；
  - 不依赖真实时间等待（mock time.monotonic）；
  - 全量测试 1090 passed, 2 skipped。
- 涉及文件路径：tests/unit/test_hotwords_cache_ttl.py（新增）。
- 关联需求/测试：FR-ASR-001；T-HW-TTL-01~05。

### TASK-0143 M10-E 发布收口（v7 审查报告 + m10e_release.json）
- 状态：DONE（2026-03-04）
- 任务描述：生成 M10-E 发布清单与第七版审查报告，完成 M10-E 里程碑收口。
- 输入：TASK-0141~0142 完成后；release_manifest.py；docs/60_trace/requirements_trace.md。
- 输出：
  - `release/manifests/m10e_release.json`（新增）：锁定 asr.py hash + test_hotwords_cache_ttl.py hash + test_passed_count=1090；
  - `docs/40_agents/full_code_review_report_2026-03-04-v7.md`（新增）：第七版审查报告；
  - `docs/60_trace/requirements_trace.md` 扩展：R-109（F-42 TTL 缓存）状态 DONE；
  - `docs/30_devspec/DevTasks.md` M10-E 状态更新为 DONE。
- 验收标准：
  - 需求追踪新增 R-109 条目完整，状态为 DONE；
  - 第七版报告更新 M10-E 改动与 Edge 优化成果；
  - m10e_release.json 包含 milestone/git hash/migration hashes/核心文件 hash/test_passed_count/generated_at。
- 涉及文件路径：docs/60_trace/requirements_trace.md；docs/40_agents/full_code_review_report_2026-03-04-v7.md；host_brain/release/release_manifest.py；release/manifests/m10e_release.json。
- 关联需求/测试：NFR-MAINT-001；T-REL-01（发布清单口径扩展）。

---

## P2 代码质量收口（F-13~F-27 批次）

### TASK-0144 P2 代码质量收口：F-16 _fetch_by_request 白名单校验补全
- 状态：DONE（2026-03-04）
- 任务描述：`_fetch_by_request()` 缺少表名白名单校验（F-16 遗漏），补充 `_ALLOWED_TABLES` 检查，与 `_fetch_all`/`fetch_by_time_range`/`count_by_time_range` 保持一致。
- 输入：risk_registry.md（R76-4）；host_brain/audit/sqlite_ledger.py。
- 输出：
  - `_fetch_by_request()` 函数头部增加 `if table not in _ALLOWED_TABLES: raise ValueError(...)` 校验；
  - 注释标注 `# F-16: validate table name with whitelist to prevent SQL injection`。
- 验收标准：
  - 传入非白名单表名时抛出 `ValueError`（含 `AUDIT_TABLE_NAME_INVALID`）；
  - 全量测试 1090 passed, 2 skipped（无回归）。
- 涉及文件路径：host_brain/audit/sqlite_ledger.py。
- 关联需求/测试：NFR-SEC-001；T-Quality-01（F-16 扩展）。

### TASK-0145 P2 状态确认：F-13/F-14/F-15/F-25/F-38 已修复核查
- 状态：DONE（2026-03-04）
- 任务描述：核查 P2 发现项中已在历次开发中修复的条目，更新 risk_registry.md 状态。
- 核查结论：
  - F-13（裸 print）：main.py 中已无 print() 调用，已修复 ✅；
  - F-14（compile_runtime_graph 类型标注）：已有 `-> Any` 标注，已修复 ✅；
  - F-15（fastapi_app.py handler 类型标注）：所有 async handler 均有返回类型标注，已修复 ✅；
  - F-25（asyncio.get_event_loop DeprecationWarning）：vts_driver.py 已改为 `asyncio.new_event_loop()`，已修复 ✅；
  - F-38（list_proposals cursor-based 分页）：已实现 `after_id` cursor 分页，已修复 ✅。
- 涉及文件路径：host_brain/app/main.py；host_brain/integrations/graph_executor.py；host_brain/app/fastapi_app.py；edge_client/actuators/vts_driver.py；host_brain/audit/sqlite_ledger.py。
- 关联需求/测试：NFR-MAINT-001。

### TASK-0146 P2 文档记录：F-18/F-19/F-20/F-26/F-27 已知边界确认
- 状态：DONE（2026-03-04）
- 任务描述：将剩余 P2 已知边界项记录为"已知限制/后续优化方向"，不阻塞当前发布。
- 已知边界确认：
  - F-18（SQLite 高并发读写瓶颈）：当前单机部署可接受；中期可引入 PostgreSQL 或只读副本；
  - F-19（WAL checkpoint 监控）：已有 close() 自动 PASSIVE checkpoint；长期运行需监控 WAL 文件大小；
  - F-20（sandbox 仅支持 Python）：非 Python 语言稳定拒绝 + reason_code 可统计；按需扩展；
  - F-26（Docker 镜像构建依赖网络）：支持预构建镜像本地加载；部署文档已标注；
  - F-27（nsjail 非 Linux 不可用）：仅作为 Linux 备选；部署文档已标注平台要求。
- 涉及文件路径：docs/60_trace/risk_registry.md（状态更新）。
- 关联需求/测试：NFR-REL-001；NFR-MAINT-001。

---

## M11 规划框架（待开发）

### M11 里程碑概览

基于 PRD.md 中尚未实现的功能需求，M11 规划如下：

| 子里程碑 | 核心需求 | 优先级 | 前置条件 |
|---------|---------|--------|---------|
| M11-A | FR-SCHED-001/002（定时调度器统一入口 + 健康状态） | P1 | M10-E DONE |
| M11-B | FR-DESKTOP-001/002（桌面留言文件创建与撤销） | P1 | M11-A DONE |
| M11-C | FR-DREAM-001/002（梦境生成 + 可回溯） | P2 | M11-A DONE |
| M11-D | FR-DIARY-001（日记生成每日定时汇总） | P2 | M11-A DONE |
| M11-E | FR-PROACTIVE-001/002（主动对话触发策略） | P2 | M11-A DONE |
| M11-F | FR-FOCUS-001（Focus Mode 状态切换） | P3 | M11-E DONE |

> M11 开发前需先完成 DevSpec §21（调度器）+ §22（桌面留言）+ §23（梦境/日记/主动对话）的工程契约拆解。

---

## M11-A 定时调度器（Scheduler）任务卡

### TASK-0147 M11-A-1：DevSpec §21~§23 工程契约拆解
- 状态：DONE（2026-03-04，DevSpec.md §21~§23 已追加）
- 任务描述：在 DevSpec.md 中追加 §21（调度器）、§22（桌面留言）、§23（梦境/日记/主动对话/Focus Mode）工程契约，包含模块边界、Schema、SQLite 表、接口契约、错误码、测试编号。
- 输入：PRD.md §4.20~§4.25（FR-SCHED/FR-DESKTOP/FR-DREAM/FR-DIARY/FR-PROACTIVE/FR-FOCUS）。
- 输出：DevSpec.md §21~§23 完整工程契约。
- 涉及文件路径：docs/30_devspec/DevSpec.md。
- 关联需求/测试：FR-SCHED-001/002；FR-DESKTOP-001/002；FR-DREAM-001/002；FR-DIARY-001；FR-PROACTIVE-001/002；FR-FOCUS-001。

### TASK-0148 M11-A-2：SchedulerEngine 核心实现
- 状态：DONE（2026-03-04，host_brain/scheduler/ 已有完整实现：NatsumeScheduler + SchedulerTaskRegistry + SchedulerHealthReporter）
- 任务描述：实现 `SchedulerEngine`（任务注册/注销/tick/健康状态）+ `SchedulerStore`（SQLite 持久化）+ `ScheduledTask`/`TaskHealth` Schema。
- 输入：DevSpec §21.1~§21.5。
- 输出：
  - `host_brain/scheduler/scheduler_engine.py`：`SchedulerEngine` 类，含 `register_task / unregister_task / enable_task / disable_task / tick / get_health / list_health / _build_system_intent`；
  - `host_brain/scheduler/scheduler_models.py`：`ScheduledTask`、`TaskHealth`、`SchedulerConfig` dataclass；
  - `host_brain/scheduler/scheduler_store.py`：`SchedulerStore`（SQLite CRUD，`scheduler_tasks` 表）；
  - `host_brain/scheduler/__init__.py`：导出 `SchedulerEngine`。
- 验收标准：
  - T-SCHED-01：注册任务后 tick() 在正确时间触发，生成 system_initiated_intent；
  - T-SCHED-02：sys_mode=GAMING 时 NORMAL 优先级任务被跳过；
  - T-SCHED-03：sys_mode=SLEEP 时 HIGH 优先级白名单任务正常触发；
  - T-SCHED-04：连续失败 MAX_ERROR_COUNT 次后 status=SUSPENDED；
  - T-SCHED-06：注销任务后 tick() 不再触发；
  - T-SCHED-07：system_initiated_intent 中 proposer_node_id="scheduler"。
- 涉及文件路径：host_brain/scheduler/scheduler_engine.py；host_brain/scheduler/scheduler_models.py；host_brain/scheduler/scheduler_store.py；host_brain/scheduler/__init__.py。
- 关联需求/测试：FR-SCHED-001；T-SCHED-01/02/03/04/06/07。

### TASK-0149 M11-A-3：Scheduler REST API + 健康端点
- 状态：DONE（2026-03-04）
- 任务描述：实现 `/scheduler/tasks` CRUD + `/scheduler/health` 端点，接入 FastAPI 路由。
- 输入：DevSpec §21.6；TASK-0148 完成。
- 输出：
  - `host_brain/scheduler/scheduler_routes.py`：8 个端点（POST/GET/PATCH/DELETE tasks + enable/disable + GET health）；
  - `host_brain/app/fastapi_app.py`：注册 scheduler_router。
- 验收标准：
  - T-SCHED-05：/scheduler/health 返回所有任务健康快照，字段完整；
  - 全量测试无回归（≥1090 passed）。
- 涉及文件路径：host_brain/scheduler/scheduler_routes.py；host_brain/app/fastapi_app.py。
- 关联需求/测试：FR-SCHED-002；T-SCHED-05。

### TASK-0150 M11-A-4：Scheduler 单元测试
- 状态：DONE（2026-03-04，tests/unit/test_scheduler_engine.py T-SCHED-01~07 全部通过，12 passed）
- 任务描述：编写 T-SCHED-01~07 全量单元测试。
- 输入：DevSpec §21.9；TASK-0148/0149 完成。
- 输出：`tests/unit/test_scheduler_engine.py`（T-SCHED-01~07，含 sys_mode 门控、连续失败暂停、system_initiated_intent 字段校验）。
- 验收标准：T-SCHED-01~07 全部通过；全量测试 ≥1097 passed（新增 7 个）。
- 涉及文件路径：tests/unit/test_scheduler_engine.py。
- 关联需求/测试：FR-SCHED-001/002；T-SCHED-01~07。

### TASK-0151 M11-B-1：DesktopMessageSkill 实现
- 状态：DONE（2026-03-04，host_brain/skills/impl/desktop_note.py 已有完整实现；tests/unit/test_desktop_note_skill.py T-DESK-01~08 全部通过）
- 任务描述：实现 `DesktopMessageSkill`（create/update/delete/undo）+ 安全路径校验 + manifest 生成。
- 输入：DevSpec §22.1~§22.6。
- 输出：
  - `host_brain/skills/desktop_message/desktop_message_skill.py`；
  - `host_brain/skills/desktop_message/desktop_message_models.py`；
  - `host_brain/skills/desktop_message/path_validator.py`；
  - `tests/unit/test_desktop_message_skill.py`（T-DESK-01~08）。
- 验收标准：T-DESK-01~08 全部通过；全量测试 ≥1105 passed。
- 涉及文件路径：host_brain/skills/desktop_message/；tests/unit/test_desktop_message_skill.py。
- 关联需求/测试：FR-DESKTOP-001/002；T-DESK-01~08。

### TASK-0152 M11-C/D-1：DreamSkill + DiarySkill 实现
- 状态：DONE（2026-03-04，host_brain/skills/impl/dream_generator.py + diary_writer.py 已有完整实现；tests/unit/test_dream_skill.py T-DREAM-01~05 + tests/unit/test_diary_skill.py T-DIARY-01~05 全部通过）
- 任务描述：实现 `DreamSkill`（Episodic 读取 → LLM 生成 → 写入 Episodic）+ `DiarySkill`（当日对话读取 → LLM 生成 → 写文件 + Episodic）。
- 输入：DevSpec §23.1~§23.2。
- 输出：
  - `host_brain/skills/dream/dream_skill.py`；
  - `host_brain/skills/diary/diary_skill.py`；
  - `tests/unit/test_dream_skill.py`（T-DREAM-01~05）；
  - `tests/unit/test_diary_skill.py`（T-DIARY-01~05）。
- 验收标准：T-DREAM-01~05 + T-DIARY-01~05 全部通过；全量测试 ≥1115 passed。
- 涉及文件路径：host_brain/skills/dream/；host_brain/skills/diary/；tests/unit/test_dream_skill.py；tests/unit/test_diary_skill.py。
- 关联需求/测试：FR-DREAM-001/002；FR-DIARY-001/002；T-DREAM-01~05；T-DIARY-01~05。

### TASK-0153 M11-E/F-1：ProactiveSkill + Focus Mode 实现
- 状态：DONE（2026-03-04，host_brain/skills/impl/proactive_talk.py + focus_mode.py 已有完整实现；tests/unit/test_proactive_skill.py T-PROACT-01~06 + tests/unit/test_focus_mode.py T-FOCUS-01~04 全部通过）
- 任务描述：实现 `ProactiveSkill`（条件校验 → LLM 生成 → 推送）+ `SysModeEnum.FOCUS` 扩展 + Focus Mode 行为矩阵。
- 输入：DevSpec §23.3~§23.4。
- 输出：
  - `host_brain/skills/proactive/proactive_skill.py`；
  - `host_brain/core/sys_mode.py`（新增 FOCUS 枚举值）；
  - `tests/unit/test_proactive_skill.py`（T-PROACT-01~06）；
  - `tests/unit/test_focus_mode.py`（T-FOCUS-01~04）。
- 验收标准：T-PROACT-01~06 + T-FOCUS-01~04 全部通过；全量测试 ≥1125 passed。
- 涉及文件路径：host_brain/skills/proactive/；host_brain/core/sys_mode.py；tests/unit/test_proactive_skill.py；tests/unit/test_focus_mode.py。
- 关联需求/测试：FR-PROACTIVE-001/002；FR-FOCUS-001；T-PROACT-01~06；T-FOCUS-01~04。

---

## M12 任务卡（PLANNED）

### TASK-0154 M12 前置：DevSpec §24 工程契约拆解
- 状态：DONE（2026-03-04，DevSpec §24.1~§24.4 已写入）
- 任务描述：拆解 M12-A~D 工程契约（ToolBroker/ContextCompressor/Reranker/SkillHotLoader），写入 DevSpec §24。
- 输入：PRD FR-TOOL-001/002/003；FR-MEM-004/005；FR-SKILL-001/002 扩展。
- 输出：DevSpec §24.1~§24.4（接口/Schema/错误码/测试编号）。
- 验收标准：DevSpec §24 完整，含 T-TOOL-01~06 / T-CTX-01~05 / T-RERANK-01~04 / T-HOTLOAD-01~05。
- 关联需求：FR-TOOL-001/002/003；FR-MEM-004/005；FR-SKILL-001/002。

### TASK-0155 M12-A-1：ToolBroker 核心实现
- 状态：DONE（2026-03-04，broker.py/catalog.py/selector.py/mcp_adapter.py 已有完整实现；T-TOOL-01~06 17 passed）
- 任务描述：实现 `ToolBroker`（单入口）+ `ToolMetaRegistry`（注册表）+ `ToolVisibility`（hash 计算）。
- 输入：DevSpec §24.1。
- 输出：
  - `host_brain/tools/tool_broker.py`；
  - `host_brain/tools/tool_meta_registry.py`；
  - `host_brain/tools/tool_visibility.py`；
  - `host_brain/core/errors.py`（新增 TOOL_* 错误码）。
- 验收标准：T-TOOL-01~06 全部通过；全量测试 ≥1170 passed。
- 关联需求/测试：FR-TOOL-001/002/003；T-TOOL-01~06。

### TASK-0156 M12-A-2：ToolBroker REST API
- 状态：DONE（2026-03-04，tool_routes.py NEW；T-TOOL-API-01~05 15 passed）
- 任务描述：实现 `tool_routes.py`（GET /tools/meta-pack + POST /tools/execute）并注册到 fastapi_app.py。
- 输入：DevSpec §24.1.3；TASK-0155。
- 输出：
  - `host_brain/tools/tool_routes.py`；
  - `host_brain/app/fastapi_app.py`（注册 tool_router）。
- 验收标准：API 端点可调用；WRITE 无 token 返回 403 + TOOL_TOKEN_MISSING。
- 关联需求/测试：FR-TOOL-003；T-TOOL-02/T-TOOL-05。

### TASK-0157 M12-B-1：ContextCompressor 实现
- 状态：DONE（2026-03-04，context_compressor.py NEW；T-CTX-01~05 17 passed）
- 任务描述：实现 `ContextCompressor`（sliding_window/summary/hybrid 三策略）+ `EvidencePack` 构建。
- 输入：DevSpec §24.2。
- 输出：
  - `host_brain/memory/context_compressor.py`；
  - `tests/unit/test_context_compressor.py`（T-CTX-01~05）。
- 验收标准：T-CTX-01~05 全部通过；LLM 不可用时降级为 sliding_window。
- 关联需求/测试：FR-MEM-005；T-CTX-01~05。

### TASK-0158 M12-C-1：RerankerService 强化
- 状态：DONE（2026-03-04，reranker.py/evidence_pack.py 已有完整实现；T-RERANK-01~04 11 passed）
- 任务描述：强化 `RerankerService`（health() + 降级策略 + evidence_pack rerank_stage 字段）。
- 输入：DevSpec §24.3；现有 `host_brain/retrieval/reranker.py`。
- 输出：
  - `host_brain/retrieval/reranker.py`（强化）；
  - `tests/unit/test_reranker_service.py`（T-RERANK-01~04）。
- 验收标准：T-RERANK-01~04 全部通过；不可用时降级且 stage 标注。
- 关联需求/测试：FR-MEM-004；T-RERANK-01~04。

### TASK-0159 M12-D-1：SkillHotLoader 实现
- 状态：DONE（2026-03-04，hot_loader.py NEW；T-HOTLOAD-01~05 17 passed）
- 任务描述：实现 `SkillHotLoader`（热加载/回滚/版本注册）+ `SkillVersionRegistry`。
- 输入：DevSpec §24.4。
- 输出：
  - `host_brain/skills/hot_loader.py`；
  - `host_brain/skills/version_registry.py`；
  - `tests/unit/test_skill_hot_loader.py`（T-HOTLOAD-01~05）。
- 验收标准：T-HOTLOAD-01~05 全部通过；热加载失败时旧版本保持不变。
- 关联需求/测试：FR-SKILL-001/002；T-HOTLOAD-01~05。

### TASK-0160 M12 错误码补全
- 状态：DONE（2026-03-04，errors.py 新增 TOOL_*/CTX_*/RETR_RERANK_STAGE_MISSING/HOTLOAD_*）
- 任务描述：在 `core/errors.py` 中补充 M12 新增错误码（TOOL_*/CTX_*/RERANK_*/HOTLOAD_*）。
- 输入：DevSpec §24.1.5；TASK-0155~0159。
- 输出：`host_brain/core/errors.py`（新增 M12 错误码）。
- 验收标准：所有 M12 错误码常量存在且有注释。
- 关联需求：FR-TOOL-001/002/003；FR-MEM-004/005；FR-SKILL-001/002。

### TASK-0161 M12 发布清单
- 状态：DONE（2026-03-04，scripts/generate_m12_release.py NEW；docs/50_release/m12_release.json 生成；11 核心文件 0 缺失；1240 passed；release_ready=true）
- 任务描述：生成 `m12_release.json`（含 M12-A~D 核心文件 hash + 测试通过数）。
- 输入：TASK-0155~0160 全部 DONE。
- 输出：`host_brain/release/manifests/m12_release.json`。
- 验收标准：manifest 可被 release_manifest.py 验证；全量测试 ≥1190 passed。
- 关联需求：NFR-REL-001。

---

## M13 任务卡（Bond 阶段自动晋级 + 长期记忆蒸馏强化）

### TASK-0162 M13-A Bond 阶段积分 + 自动晋级引擎
- 状态：DONE（2026-03-04，bond_stage_engine.py NEW；bond_routes.py NEW；T-BOND-STAGE-01~09 30 passed；T-BOND-API-01~08 18 passed）
- 任务描述：实现 BondStageEngine（积分累积/阶段晋级/降级/趋势/审计串联）+ Bond REST API（/bond/stage /bond/summary /bond/event /bond/users /bond/health）。
- 输入：FR-BOND-001/002/003；DevSpec §25（新增）。
- 输出：
  - `host_brain/bond/bond_stage_engine.py`（BondStageEngine + BondScoreState + StageTransitionResult）；
  - `host_brain/app/bond_routes.py`（Bond REST API 5 端点）；
  - `tests/unit/test_bond_stage_engine.py`（T-BOND-STAGE-01~09，30 passed）；
  - `tests/integration/test_bond_routes_api.py`（T-BOND-API-01~08，18 passed）。
- 验收标准：T-BOND-STAGE-01~09 + T-BOND-API-01~08 全部通过；Bond Summary 不含数值（FR-BOND-003）；evidence_refs 为空时 422（FR-BOND-002）；晋级/降级写入审计链。
- 关联需求：FR-BOND-001/002/003；T-BOND-STAGE-01~09；T-BOND-API-01~08。

---

### TASK-0163 M13-B 长期记忆蒸馏强化
- 状态：DONE（2026-03-04，long_term_distiller.py NEW；T-DISTILL-B-01~10 36 passed）
- 任务描述：实现 LongTermMemoryDistiller（EpisodicMemory→语义记忆蒸馏→Canon pending 提案）+ 调度器 CRON 任务工厂（每日 03:00）。
- 输入：FR-MEM-DISTILL-001/002；DevSpec §26（新增）；MemoryDistillationEngine（M10-C/D）；WorldCanonStore（M10-B）；NatsumeScheduler（M11）。
- 输出：
  - `host_brain/memory/long_term_distiller.py`（LongTermMemoryDistiller + DistillationCycleResult + make_distillation_task_config）；
  - `tests/unit/test_long_term_distiller.py`（T-DISTILL-B-01~10，36 passed）。
- 验收标准：T-DISTILL-B-01~10 全部通过；蒸馏结果只生成 pending 提案不直接写 SSOT（FR-MEM-DISTILL-001）；每次循环最多 5 个提案（FR-MEM-DISTILL-002）；幂等保护；调度器 CRON 任务配置合法；全量测试 1324 passed。
- 关联需求/测试：FR-MEM-DISTILL-001/002；T-DISTILL-B-01~10。

---

### TASK-0164 M13-C Bond 事件与 BondDynamicsWriter 端到端集成
- 状态：DONE（2026-03-04，bond_dynamics.py 更新；T-BOND-INTEG-01~08 18 passed）
- 任务描述：将 BondStageEngine.apply_event 接入 BondDynamicsWriter.apply_update 成功路径，实现端到端联动；可选注入（stage_engine=None 向后兼容）；stage_engine 异常不影响主流程（非致命）；event 中新增 stage_transition 字段。
- 输入：BondDynamicsWriter（M13-A 前）；BondStageEngine（M13-A）。
- 输出：
  - `host_brain/bond/bond_dynamics.py`（新增 stage_engine 可选注入 + apply_event 调用）；
  - `tests/integration/test_bond_dynamics_stage_integration.py`（T-BOND-INTEG-01~08，18 passed）。
- 验收标准：T-BOND-INTEG-01~08 全部通过；向后兼容（无 stage_engine 时正常工作）；stage_engine 异常非致命；全量测试 1342 passed。
- 关联需求/测试：FR-BOND-001/002；T-BOND-INTEG-01~08。

---

### TASK-0165 M13-D M13 发布清单
- 状态：DONE（2026-03-04，m13_release.json NEW）
- 任务描述：生成 M13 发布清单，记录所有里程碑状态、核心文件 SHA-256 hash、测试通过数、质量门禁。
- 输入：M13-A/B/C 全部完成；全量测试 1342 passed。
- 输出：`docs/50_release/m13_release.json`（4 个里程碑 DONE；4 个核心文件 hash；102 个 M13 测试通过；1342 全量通过）。
- 验收标准：release_ready=true；all_core_files_present=true；zero_regressions=true。
- 关联需求/测试：M13-A/B/C/D 全部 DONE。

---

## M14 任务卡（通道 + 外显 + MCP 治理收口）

### TASK-0166 M14 前置：DevSpec §27 工程契约拆解
- 状态：DONE（2026-03-04，DevSpec §25/§26/§27 NEW）
- 任务描述：拆解 M14-A~C 工程契约（WeChat 通道事件化 / VTS 外显映射 / MCP 治理），写入 DevSpec §27。
- 输入：PRD FR-CHANNEL-001；FR-AVATAR-001；FR-MCP-001。
- 输出：DevSpec §27.1~§27.3（接口/Schema/错误码/测试编号）。
- 验收标准：DevSpec §27 完整，含 T-CHAN-01~05 / T-VTS-01~05 / T-MCP-GOV-01~05。
- 关联需求：FR-CHANNEL-001；FR-AVATAR-001；FR-MCP-001。

### TASK-0167 M14-A 微信通道 I/O 事件化
- 状态：DONE（2026-03-04）
- 任务描述：将微信收发建模为确定性通道事件（inbound/outbound），写入可追溯审计链；LLM 不得自行决定"是否发送/发给谁/发什么"。
- 输入：DevSpec §27.1；现有 edge_client/channels/wechat.py（或等价模块）。
- 输出：
  - `host_brain/channels/wechat_channel.py`（WeChatChannelEvent：inbound/outbound + thread_key + request_id + audit 串联）；
  - `host_brain/core/errors.py`（新增 CHANNEL_* 错误码）；
  - `tests/unit/test_wechat_channel.py`（T-CHAN-01~05）；
  - `tests/integration/test_wechat_channel_audit.py`（T-CHAN-AUDIT-01~03）。
- 验收标准：
  - T-CHAN-01~05 全部通过；
  - 任一出站消息可在审计链追溯到 thread_key + request_id；
  - 失败必须可解释（reason_code + stage）；
  - 全量测试 ≥1350 passed。
- 关联需求/测试：FR-CHANNEL-001；T-CHAN-01~05；T-CHAN-AUDIT-01~03。

### TASK-0168 M14-B VTS 外显确定性映射
- 状态：DONE（2026-03-04）
- 任务描述：实现 emotion_tag→expression_id 固定枚举映射；拒绝非法 tag 或自由动作参数；同版本同输入输出一致。
- 输入：DevSpec §27.2；现有 edge_client/actuators/vts_driver.py。
- 输出：
  - `edge_client/actuators/vts_expression_map.py`（VTSExpressionMap：枚举映射表 + 校验）；
  - `edge_client/actuators/vts_driver.py`（强化：接入 VTSExpressionMap + 非法 tag 拒绝）；
  - `host_brain/core/errors.py`（新增 VTS_EXPRESSION_INVALID / VTS_TAG_UNKNOWN）；
  - `tests/unit/test_vts_expression_map.py`（T-VTS-01~05）。
- 验收标准：
  - T-VTS-01~05 全部通过；
  - 非法 emotion_tag/自由动作参数被拒绝并产生可统计 reason_code；
  - 合法触发可回放到固定映射表（同版本同输入输出一致）；
  - 全量测试 ≥1360 passed。
- 关联需求/测试：FR-AVATAR-001；T-VTS-01~05。

### TASK-0169 M14-C MCP Provider 治理完整验收
- 状态：DONE（2026-03-04）
- 任务描述：完整验收 MCP provider 元数据同步 + READ/WRITE 分级 + ToolBroker 单入口 + 审计串联 + AIRLOCK 出站最小化。
- 输入：DevSpec §27.3；现有 host_brain/tools/mcp_adapter.py；ToolBroker（M12-A）。
- 输出：
  - `host_brain/tools/mcp_adapter.py`（强化：provider_id/tool_id/request_id/evidence_ref 审计串联 + AIRLOCK 出站最小化）；
  - `tests/unit/test_mcp_governance.py`（T-MCP-GOV-01~05）；
  - `tests/integration/test_mcp_audit_chain.py`（T-MCP-AUDIT-01~03）。
- 验收标准：
  - T-MCP-GOV-01~05 全部通过；
  - MCP WRITE 在无 token 或未 APPROVE 时必拒绝且可统计；
  - 所有 MCP 调用可追溯到 intent/governor/skill_exec 记录；
  - 满足 AIRLOCK 出站最小化要求；
  - 全量测试 ≥1370 passed。
- 关联需求/测试：FR-MCP-001；T-MCP-GOV-01~05；T-MCP-AUDIT-01~03。

### TASK-0170 M14-D M14 发布清单
- 状态：DONE（2026-03-04，m14_release.json NEW）
- 任务描述：生成 M14 发布清单（m14_release.json），记录 M14-A~C 核心文件 hash + 测试通过数 + 质量门禁。
- 输入：TASK-0166~0169 全部 DONE。
- 输出：`docs/50_release/m14_release.json`。
- 验收标准：release_ready=true；all_core_files_present=true；zero_regressions=true。
- 关联需求：NFR-REL-001；FR-CHANNEL-001；FR-AVATAR-001；FR-MCP-001。

---

## M15 任务卡（语音链路深化完整验收）

### TASK-0171 M15 前置：DevSpec §28 工程契约拆解
- 状态：DONE（2026-03-04，DevSpec §28 NEW）
- 任务描述：拆解 M15-A~E 工程契约（ASR 事件化 / TTS 播报队列 / Barge-in / 兜底模型 / 资源闸门），写入 DevSpec §28。
- 输入：PRD FR-VOICE-001~005。
- 输出：DevSpec §28.1~§28.6（接口/Schema/错误码/测试编号）。
- 验收标准：DevSpec §28 完整，含 T-ASR-EVT-01~05 / T-TTS-QUEUE-01~05 / T-BARGE-01~04 / T-FALLBACK-01~05 / T-RGATE-01~05。
- 关联需求：FR-VOICE-001~005。

### TASK-0172 M15-A ASR 事件化上送强化
- 状态：DONE（2026-03-04）
- 任务描述：强化 ASRTranscriptEvent Schema（alternates + evidence_ref + low_confidence 显式标注）；低置信度必须标注 reason_code=ASR_LOW_CONFIDENCE。
- 输入：DevSpec §28.1；现有 edge_client/senses/asr.py。
- 输出：
  - `edge_client/senses/asr.py`（ASRTranscriptEvent 强化：alternates/evidence_ref/low_confidence）；
  - `tests/unit/test_asr_event_emitter.py`（T-ASR-EVT-01~05）。
- 验收标准：T-ASR-EVT-01~05 全部通过；低置信度显式标注；全量测试 ≥1430 passed。
- 关联需求/测试：FR-VOICE-001；T-ASR-EVT-01~05。

### TASK-0173 M15-B TTS 播报队列强化
- 状态：DONE（2026-03-04）
- 任务描述：强化 TTSPlaybackEvent Schema（request_id 关联 + 失败 reason_code + stage）；Host 只下发最终口径文本与情绪标签。
- 输入：DevSpec §28.2；现有 edge_client/actuators/tts_engine.py。
- 输出：
  - `edge_client/actuators/tts_engine.py`（TTSPlaybackEvent 强化：request_id/reason_code/stage）；
  - `tests/unit/test_tts_playback_queue.py`（T-TTS-QUEUE-01~05）。
- 验收标准：T-TTS-QUEUE-01~05 全部通过；播报事件可关联 request_id；全量测试 ≥1435 passed。
- 关联需求/测试：FR-VOICE-002；T-TTS-QUEUE-01~05。

### TASK-0174 M15-C Barge-in 可打断强化
- 状态：DONE（2026-03-04）
- 任务描述：强化 interrupt() 方法，补充 BargeInEvent 审计记录；打断不构成执行事实；ASR 不被阻塞。
- 输入：DevSpec §28.3；现有 edge_client/actuators/tts_engine.py。
- 输出：
  - `edge_client/actuators/tts_engine.py`（interrupt() 强化：BargeInEvent 写入审计链）；
  - `tests/unit/test_barge_in.py`（T-BARGE-01~04）。
- 验收标准：T-BARGE-01~04 全部通过；打断事件可追溯；全量测试 ≥1439 passed。
- 关联需求/测试：FR-VOICE-003；T-BARGE-01~04。

### TASK-0175 M15-D Edge 兜底小模型门控
- 状态：DONE（2026-03-04）
- 任务描述：实现 FallbackModelGate（默认卸载/按需加载/draft_only 标记/快速失败）；不可用时不阻塞主流程。
- 输入：DevSpec §28.4。
- 输出：
  - `edge_client/fallback_model.py`（FallbackModelGate + FallbackOutput）；
  - `tests/unit/test_fallback_model_gate.py`（T-FALLBACK-01~05）。
- 验收标准：T-FALLBACK-01~05 全部通过；输出永远 draft_only=True；全量测试 ≥1444 passed。
- 关联需求/测试：FR-VOICE-004；T-FALLBACK-01~05。

### TASK-0176 M15-E Edge 资源闸门强化
- 状态：DONE（2026-03-04）
- 任务描述：强化 EdgeResourceGate，补充优先级抢占审计（ResourcePreemptionEvent）；压力下保证 ASR。
- 输入：DevSpec §28.5；现有 edge_client/resource_gate.py（或等价模块）。
- 输出：
  - `edge_client/resource_gate.py`（ResourcePreemptionEvent + 优先级抢占审计）；
  - `tests/unit/test_edge_resource_gate.py`（T-RGATE-01~05）。
- 验收标准：T-RGATE-01~05 全部通过；ASR 始终保证；全量测试 ≥1449 passed。
- 关联需求/测试：FR-VOICE-005；T-RGATE-01~05。

### TASK-0177 M15-F M15 发布清单
- 状态：DONE（2026-03-04，m15_release.json NEW）
- 任务描述：生成 M15 发布清单（m15_release.json），记录 M15-A~E 核心文件 hash + 测试通过数 + 质量门禁。
- 输入：TASK-0171~0176 全部 DONE。
- 输出：`docs/50_release/m15_release.json`。
- 验收标准：release_ready=true；all_core_files_present=true；zero_regressions=true；total_tests_passed ≥ 1445。
- 关联需求：NFR-REL-001；FR-VOICE-001~005。

---

## M16 Canon 治理强化 + 记忆检索优化 + 调度器持久化

### TASK-0178 M16-A Canon 治理 API 强化
- 状态：DONE
- 任务描述：重构 canon_routes.py，修复 _get_canon_store() 全局单例竞态（F-28）；修复 _get_ledger_from_app() 延迟导入循环引用风险（F-29）；approve/reject 端点两次 DB 操作改为事务包裹（F-35）；reject 审计写入失败纳入事务确保原子性（F-36）。
- 输入：DevSpec §20；现有 host_brain/app/canon_routes.py；F-28/F-29/F-35/F-36 发现项。
- 输出：
  - `host_brain/app/canon_routes.py`（依赖注入重构 + 事务包裹）；
  - `tests/unit/test_canon_routes_tx.py`（T-CANON-TX-01~06）。
- 验收标准：T-CANON-TX-01~06 全部通过；approve/reject 操作原子性保证；全量测试 ≥1539 passed。
- 关联需求/测试：FR-CANON-GOV-001；T-CANON-TX-01~06。

### TASK-0179 M16-B 长期记忆检索强化
- 状态：DONE
- 任务描述：重构 canon_world_store.py 的 list_proposals()，改为 cursor-based 分页（F-38）；补充 cursor 参数（after_id）；深页码性能从 O(N) 降为 O(1)。
- 输入：DevSpec §21；现有 host_brain/memory/canon_world_store.py；F-38 发现项。
- 输出：
  - `host_brain/memory/canon_world_store.py`（list_proposals() cursor-based 分页）；
  - `tests/unit/test_canon_cursor_pagination.py`（T-CURSOR-01~05）。
- 验收标准：T-CURSOR-01~05 全部通过；after_id 参数可选（向后兼容）；全量测试 ≥1544 passed。
- 关联需求/测试：FR-MEM-PAGE-001；T-CURSOR-01~05。

### TASK-0180 M16-C 调度器任务持久化
- 状态：DONE
- 任务描述：实现 NatsumeScheduler CRON 任务 SQLite 持久化（F-40）；进程重启后自动从 DB 恢复任务注册；新增 scheduler_tasks 表（migration 0018）。
- 输入：DevSpec §22；现有 host_brain/scheduler/scheduler.py；F-40 发现项。
- 输出：
  - `host_brain/scheduler/scheduler.py`（持久化 + 重启恢复）；
  - `host_brain/audit/migrations/0018_scheduler_tasks.sql`（scheduler_tasks 表）；
  - `tests/unit/test_scheduler_persistence.py`（T-SCHED-PERSIST-01~05）。
- 验收标准：T-SCHED-PERSIST-01~05 全部通过；重启后任务自动恢复；全量测试 ≥1549 passed。
- 关联需求/测试：FR-SCHED-PERSIST-001；T-SCHED-PERSIST-01~05。

### TASK-0181 M16-D M16 发布清单
- 状态：DONE
- 任务描述：生成 M16 发布清单（m16_release.json），记录 M16-A~C 核心文件 hash + 测试通过数 + 质量门禁。
- 输入：TASK-0178~0180 全部 DONE。
- 输出：`docs/50_release/m16_release.json`。
- 验收标准：release_ready=true；all_core_files_present=true；zero_regressions=true；total_tests_passed ≥ 1549。
- 关联需求：NFR-REL-001；FR-CANON-GOV-001；FR-MEM-PAGE-001；FR-SCHED-PERSIST-001。

---

## M17 监控面板 v3 — 全服务监控 + Dify 风格链路可视化（前端重建）（DONE，2026-03-04）

> 目标：将现有 panel.html 单页重建为专业运维监控 SPA，参考 Dify 简约风格，实现双端全服务监控 + 节点链路可视化展示。
> 依赖：M18 后端 API（可并行开发，前端先用 mock 数据）。
> 所有任务当前状态：DONE（2026-03-04：React+Vite SPA 构建成功；四页面全量可用；m17_release.json 已生成）。

### 核心技术栈约定（M17 强制执行）

| 技术 | 版本 | 用途 |
|------|------|------|
| **React** | 18.x | 组件化开发，所有页面均为 React 组件 |
| **Vite** | 5.x | 快速构建；`npm run build` 产出静态文件由 FastAPI StaticFiles 托管 |
| **Tailwind CSS** | 3.x | 原子化样式；实现 Dify 简约白色风格；**严格禁用大面积高饱和度渐变与廉价发光阴影** |
| **React Router** | 6.x | 4 个核心页面的 History/Hash 路由切换 |
| **React Flow** | latest | Trace 节点流图（Dify Workflow 同款底层库，原生支持节点卡片、有向连线、分支/并行布局） |
| **Apache ECharts** | 5.x | Metrics 时序折线图、柱状图、条形图；性能优秀，抗锯齿效果佳 |
| **Zustand** | 4.x | 全局状态管理（30s 轮询状态 + 全局时间范围过滤）；轻量，无 Redux 样板代码 |
| **Lucide React** | latest | 统一专业的纯色 SVG 图标；**禁止使用 Emoji 作为图标** |

**项目结构约定：**
- 源码目录：`host_brain/app/monitor_ui/`（React 项目根，含 `package.json` / `vite.config.js`）
- 构建产出：`host_brain/app/static/monitor/`（FastAPI StaticFiles 托管，入口为 `index.html`）
- 开发模式：`npm run dev`（Vite dev server，代理 `/obs/*` 到 FastAPI）
- 生产构建：`npm run build`（产出物提交到仓库，无需 Node.js 运行时）

### TASK-0182 M17-A 前端架构重建（React + Vite 项目初始化 + 设计系统 + 路由 + 布局框架）
- 状态：DONE
- 任务描述：
  初始化 React 18 + Vite 5 项目（`host_brain/app/monitor_ui/`）；配置 Tailwind CSS（Dify 设计 token：背景 `#F9FAFB`，主色 `#2970FF`，卡片圆角 `8px`，字体 Inter）；配置 React Router 6（4 个页面路由）；建立左侧固定导航栏（Lucide React 图标）+ 顶部面包屑 + 主内容区布局框架；配置 Zustand store（轮询状态 + 时间范围过滤）；配置 Vite 构建产出到 `host_brain/app/static/monitor/`；所有页面先用 mock 数据占位。
- 输入：DevSpec §OBS-V3-001；FR-OBS-003；Dify 设计参考（白色简约风格）；技术栈约定（见上方）。
- 输出：
  - `host_brain/app/monitor_ui/package.json`（依赖声明：react/vite/tailwind/react-router/reactflow/echarts/zustand/lucide-react）；
  - `host_brain/app/monitor_ui/vite.config.js`（构建配置，产出到 `../static/monitor/`）；
  - `host_brain/app/monitor_ui/tailwind.config.js`（Dify 设计 token）；
  - `host_brain/app/monitor_ui/src/main.jsx`（React 入口）；
  - `host_brain/app/monitor_ui/src/App.jsx`（根组件 + React Router 路由配置）；
  - `host_brain/app/monitor_ui/src/store/index.js`（Zustand store）；
  - `host_brain/app/monitor_ui/src/components/Layout.jsx`（导航栏 + 布局框架）；
  - `host_brain/app/monitor_ui/src/pages/Overview.jsx`（mock 占位）；
  - `host_brain/app/monitor_ui/src/pages/Sessions.jsx`（mock 占位）；
  - `host_brain/app/monitor_ui/src/pages/Trace.jsx`（mock 占位）；
  - `host_brain/app/monitor_ui/src/pages/Metrics.jsx`（mock 占位）；
  - `host_brain/app/static/monitor/`（`npm run build` 产出，提交到仓库）。
- 验收标准：
  - `npm run dev` 启动无报错，四个页面均可通过路由切换；
  - `npm run build` 成功产出，FastAPI 可正常托管；
  - 左侧导航高亮当前页，Lucide React 图标正常显示；
  - 在 1280×800 分辨率下布局正常，无横向滚动条；
  - 设计风格符合 Dify 简约白色风格（非深色主题，无高饱和度渐变）。
- 关联需求/测试：FR-OBS-003；T-OBS-V3-ARCH-01~03。

### TASK-0183 M17-B 全服务健康总览页（Overview Page）
- 状态：DONE
- 任务描述：
  实现 Overview 页面，展示双端所有服务健康状态卡片网格；顶部汇总统计行（总服务数/健康/降级/故障）；每个服务卡片含：服务名、侧（Host/Edge）、状态徽章（UP/DOWN/DEGRADED）、响应延迟、最后检查时间、错误摘要（DOWN 时展示）；30 秒自动轮询 `/obs/services/health`（M18-A 提供，开发阶段使用 mock）。
- 输入：TASK-0182 完成；DevSpec §OBS-V3-002；FR-OBS-004。
- 输出：
  - `host_brain/app/static/pages/overview.js`（完整实现，对接真实 API）；
  - 服务卡片组件（内联于 overview.js）。
- 验收标准：
  - 所有已注册服务（≥12 个）均出现在总览页；
  - DOWN 状态卡片显示红色边框 + 错误摘要；
  - 轮询失败时显示"上次已知状态 HH:MM:SS"；
  - 顶部汇总数字与卡片数量一致。
- 关联需求/测试：FR-OBS-004；T-OBS-V3-OVW-01~05。

### TASK-0184 M17-C 会话列表页（Sessions Page）
- 状态：DONE
- 任务描述：
  实现 Sessions 页面，展示历史会话列表；顶部过滤栏（时间范围选择器 + 状态下拉 + 关键词搜索）；表格展示（request_id / 时间 / 状态 / 耗时 / 通道 / reason_code）；底部分页控件；点击行跳转到 Trace 页并传递 request_id；对接 `/obs/sessions` API（开发阶段使用 mock）。
- 输入：TASK-0182 完成；DevSpec §OBS-V3-003；FR-OBS-005。
- 输出：
  - `host_brain/app/static/pages/sessions.js`（完整实现，对接真实 API）。
- 验收标准：
  - 列表正常分页（每页 20 条），翻页不丢失过滤条件；
  - 点击行后 URL hash 变为 `#trace?rid=xxx`，Trace 页自动加载；
  - 状态过滤（OK/REJECT/ERROR）正确生效；
  - 空结果时显示友好空状态（非报错）。
- 关联需求/测试：FR-OBS-005；T-OBS-V3-SESS-01~05。

### TASK-0185 M17-D Dify 风格链路可视化页（Trace Page — React Flow）
- 状态：DONE
- 任务描述：
  基于 **React Flow** 实现 Trace 页面核心功能——节点流图可视化：
  1. **React Flow 画布**：自动布局（从左到右，Dagre 布局算法），支持分支/并行，支持缩放/平移；
  2. **自定义节点卡片**（CustomNode 组件）：圆角矩形，含 Lucide React 节点类型图标、节点名称、状态颜色（绿/红/橙/蓝动画）、耗时标签、Provider 徽章（LLM 节点专属）；
  3. **有向连线**：React Flow 内置 Edge，支持分支合并，箭头样式；
  4. **节点点击侧边面板**（NodeDetailPanel 组件）：展示 input/output（JSON 格式化 + 折叠/展开）、status、reason_code、时间戳、side（Host/Edge）、model_id（LLM 节点）；
  5. **顶部工具栏**：request_id 输入框 + 查询按钮 + 全屏切换（Lucide React 图标）；
  6. 对接 `/obs/trace/{request_id}/nodes` API（开发阶段使用 mock，含完整 ASR→LLM×N→TTS→VTS 链路）。
- 输入：TASK-0182 完成；DevSpec §OBS-V3-004；FR-OBS-006；React Flow 文档。
- 输出：
  - `host_brain/app/monitor_ui/src/pages/Trace.jsx`（完整实现，含 React Flow 画布）；
  - `host_brain/app/monitor_ui/src/components/trace/CustomNode.jsx`（自定义节点卡片组件）；
  - `host_brain/app/monitor_ui/src/components/trace/NodeDetailPanel.jsx`（节点详情侧边面板）；
  - `host_brain/app/monitor_ui/src/utils/traceLayout.js`（Dagre 自动布局工具函数）。
- 验收标准：
  - 给定 request_id 后节点图正确渲染（≤50 节点流畅，无卡顿）；
  - 点击节点侧边面板正确显示 input/output；
  - 并行节点以分支形式展示（不重叠，Dagre 布局正确）；
  - JSON 折叠/展开功能正常；
  - 全屏模式可用；
  - 空链路时显示友好空状态；
  - LLM 节点卡片显示 Provider 徽章（OpenAI/Gemini/DeepSeek 颜色区分）。
- 关联需求/测试：FR-OBS-006；T-OBS-V3-TRACE-01~08。

### TASK-0186 M17-E 指标大盘页（Metrics Page）
- 状态：DONE
- 任务描述：
  实现 Metrics 页面，包含：
  1. 时间范围选择器（1h / 6h / 24h / 7d）；
  2. 请求量趋势折线图（Canvas）；
  3. 拒绝率趋势折线图（Canvas）；
  4. reason_code 分布横向条形图（Canvas）；
  5. 各服务响应延迟 P50/P95 柱状图（Canvas）；
  6. 对接 `/obs/metrics/timeseries` API（开发阶段使用 mock）。
- 输入：TASK-0182 完成；DevSpec §OBS-V3-005；FR-OBS-007；Apache ECharts 文档。
- 输出：
  - `host_brain/app/monitor_ui/src/pages/Metrics.jsx`（完整实现，含 ECharts 图表组件）；
  - `host_brain/app/monitor_ui/src/components/charts/LineChart.jsx`（ECharts 折线图封装组件，可复用）；
  - `host_brain/app/monitor_ui/src/components/charts/BarChart.jsx`（ECharts 柱状/条形图封装组件，可复用）。
- 验收标准：
  - 切换时间范围后图表数据正确更新（Zustand 状态驱动）；
  - 图表在 HiDPI 屏幕下清晰（ECharts 内置 devicePixelRatio 支持）；
  - 无数据时显示"数据采集中"占位，不崩溃；
  - 四种图表类型均正确渲染。
- 关联需求/测试：FR-OBS-007；T-OBS-V3-METRICS-01~05。

### TASK-0187 M17-F M17 发布清单
- 状态：DONE
- 任务描述：生成 M17 发布清单（m17_release.json），记录 M17-A~E 核心文件清单 + 页面功能验收状态 + 质量门禁。
- 输入：TASK-0182~0186 全部 DONE。
- 输出：`docs/50_release/m17_release.json`。
- 验收标准：release_ready=true；all_pages_functional=true；zero_js_errors=true；design_review_passed=true。
- 关联需求：FR-OBS-003~007；NFR-MAINT-001。

---

## M18 监控面板 v3 后端 API 扩展（DONE，2026-03-04）

> 目标：为 M17 前端提供完整的数据支撑 API，扩展现有 observability 路由，新增全服务健康聚合、会话列表、链路节点详情、指标时序四类端点。
> 依赖：M17 可并行开发（前端先用 mock）；M18 完成后前端切换真实 API。
> 所有任务当前状态：DONE（2026-03-04：26 个新增测试全部通过；1577 全量通过，2 skipped，0 failed；m18_release.json 已生成）。

### TASK-0188 M18-A 全服务健康聚合 API
- 状态：DONE
- 任务描述：
  实现 `GET /obs/services/health` 端点，并发探测双端所有已注册服务健康状态（Host 侧：LLM Gateway、Neo4j、SQLite、Chroma、Scheduler、Bond Engine、Memory Distiller、MCP Provider；Edge 侧：ASR、TTS、VTS、Audio Playback、Edge RPC）；聚合返回统一结构；单服务超时（≤200ms）不阻塞其他服务；支持 `?side=host|edge` 过滤。
- 输入：DevSpec §OBS-V3-API-001；FR-OBS-008；现有 `host_brain/app/obs_routes.py`（或等价）。
- 输出：
  - `host_brain/app/obs_routes.py`（新增 `/obs/services/health` 端点）；
  - `host_brain/obs/service_health_aggregator.py`（ServiceHealthAggregator：并发探测 + 结果聚合）；
  - `tests/unit/test_service_health_aggregator.py`（T-OBS-HEALTH-01~06）。
- 验收标准：
  - T-OBS-HEALTH-01~06 全部通过；
  - 单服务超时时该服务 status=DOWN，其余正常返回；
  - 响应时间 ≤500ms（并发探测）；
  - 响应结构稳定（字段不随服务状态变化而缺失）；
  - 全量测试 ≥1555 passed。
- 关联需求/测试：FR-OBS-008；T-OBS-HEALTH-01~06。

### TASK-0189 M18-B 会话列表 API
- 状态：DONE
- 任务描述：
  实现 `GET /obs/sessions` 端点，从 SQLite Ledger 查询会话列表；支持过滤参数：start_time、end_time、status、channel、keyword；分页参数：page、page_size；响应包含 total_count；查询性能优化（必要时补充索引）。
- 输入：DevSpec §OBS-V3-API-002；FR-OBS-009；现有 SQLite Ledger schema。
- 输出：
  - `host_brain/app/obs_routes.py`（新增 `/obs/sessions` 端点）；
  - `host_brain/obs/session_query.py`（SessionQueryService：过滤 + 分页 + 性能优化）；
  - `host_brain/audit/migrations/0019_obs_sessions_index.sql`（sessions 查询索引）；
  - `tests/unit/test_session_query.py`（T-OBS-SESS-01~06）。
- 验收标准：
  - T-OBS-SESS-01~06 全部通过；
  - 过滤参数组合正确生效；
  - total_count 与实际数据一致；
  - 空结果时返回 results=[] 而非 404；
  - 全量测试 ≥1561 passed。
- 关联需求/测试：FR-OBS-009；T-OBS-SESS-01~06。

### TASK-0190 M18-C 链路节点详情 API
- 状态：DONE
- 任务描述：
  实现 `GET /obs/trace/{request_id}/nodes` 端点，从审计链（intent_log / governor_log / skill_exec_log / asr_log / tts_log / vts_log 等）聚合构建结构化节点列表；每个节点包含 node_id、node_type、node_name、status、start_ts_ms、end_ts_ms、duration_ms、input、output、reason_code、parent_node_id、side；支持 `?include_io=false` 参数；input/output 超 64KB 截断并标注 truncated=true。
- 输入：DevSpec §OBS-V3-API-003；FR-OBS-010；现有审计链 schema（intent_log/governor_log/skill_exec_log）。
- 输出：
  - `host_brain/app/obs_routes.py`（新增 `/obs/trace/{request_id}/nodes` 端点）；
  - `host_brain/obs/trace_node_builder.py`（TraceNodeBuilder：多源日志聚合 + 节点树构建）；
  - `tests/unit/test_trace_node_builder.py`（T-OBS-TRACE-01~08）。
- 验收标准：
  - T-OBS-TRACE-01~08 全部通过；
  - 给定有效 request_id 返回完整节点列表；
  - parent_node_id 正确反映执行树结构；
  - 不存在的 request_id 返回 404 + 稳定错误结构；
  - input/output 超限时 truncated=true 标注存在；
  - 全量测试 ≥1569 passed。
- 关联需求/测试：FR-OBS-010；T-OBS-TRACE-01~08。

### TASK-0191 M18-D 指标时序 API
- 状态：DONE
- 任务描述：
  实现 `GET /obs/metrics/timeseries` 端点，支持多维度时序数据查询（request_count / reject_rate / latency_p50 / latency_p95 / reason_code_dist）；支持 interval（1m/5m/1h）、start_time、end_time、多 metric 批量查询；数据点上限 1000（超出自动降采样）；无数据区间返回 null 值点保证时序连续性。
- 输入：DevSpec §OBS-V3-API-004；FR-OBS-011；现有 SQLite Ledger 审计数据。
- 输出：
  - `host_brain/app/obs_routes.py`（新增 `/obs/metrics/timeseries` 端点）；
  - `host_brain/obs/metrics_timeseries.py`（MetricsTimeseriesService：时序聚合 + 降采样 + 批量查询）；
  - `tests/unit/test_metrics_timeseries.py`（T-OBS-TS-01~06）。
- 验收标准：
  - T-OBS-TS-01~06 全部通过；
  - 时序数据点时间间隔与 interval 参数一致；
  - 降采样后数据点数量 ≤1000；
  - 批量查询返回 {metrics: {metric_name: {data_points: [...]}}} 结构；
  - 全量测试 ≥1575 passed。
- 关联需求/测试：FR-OBS-011；T-OBS-TS-01~06。

### TASK-0192 M18-E M18 发布清单
- 状态：DONE
- 任务描述：生成 M18 发布清单（m18_release.json），记录 M18-A~D 核心文件 hash + 测试通过数 + 质量门禁；同时更新 UI_UPGRADE_CHANGELOG.md 记录 v3.0 变更。
- 输入：TASK-0188~0191 全部 DONE；TASK-0182~0186 全部 DONE（M17 前端切换真实 API 验证）。
- 输出：
  - `docs/50_release/m18_release.json`；
  - `docs/50_project/UI_UPGRADE_CHANGELOG.md`（v3.0 条目）。
- 验收标准：release_ready=true；all_api_endpoints_tested=true；frontend_api_integration_verified=true；zero_regressions=true；total_tests_passed ≥ 1575。
- 关联需求：FR-OBS-003~011；NFR-REL-001；NFR-MAINT-001。

---

## M19 审查报告缺陷修复（P0/P1）（DONE）

> 目标：修复 M17/M18 代码审查报告（full_code_review_report_2026-03-04-m17m18.md）中识别的全部 P0 级 Bug 和 P1 级架构问题，消除前后端字段名不一致、Route/Service 层脱节、全量数据加载等核心缺陷，提升监控面板可靠性与可维护性。
> 依赖：M17、M18 全部 DONE。
> 关联审查项：BUG-BE-001、BUG-BE-002、BUG-FE-001、WARN-FE-004、WARN-FE-005、WARN-FE-006、WARN-BE-001~003、WARN-BE-005、WARN-TEST-001~002。

### TASK-0193 M19-A 修复 Trace 降级路径 AttributeError（BUG-BE-001）
- 状态：DONE
- 任务描述：
  修复 `obs_routes.py` 中 `GET /obs/trace/{request_id}` 端点的降级路径（`row is None` 分支）：将直接属性访问 `ledger.governor_log` 改为正确的方法调用 `ledger.fetch_by_time_range("governor_log", 0, _now_ms())`，消除 `AttributeError` → 500 错误。同步检查同一端点中其他可能的直接属性访问，确保全部改为方法调用。
- 输入：`host_brain/app/obs_routes.py`；`host_brain/audit/sqlite_ledger.py`（确认可用方法签名）；审查报告 BUG-BE-001。
- 输出：
  - `host_brain/app/obs_routes.py`（降级路径修复）；
  - `tests/unit/test_obs_routes_trace_fallback.py`（T-OBS-ROUTE-TRACE-FALLBACK-01~03：验证降级路径正常返回、不存在 request_id 返回 404、ledger 方法调用正确）。
- 验收标准：
  - 降级路径（`row is None`）触发时不抛出 `AttributeError`，返回正常 JSON 响应；
  - 不存在的 request_id 返回 404 + `{"error": "not_found", "request_id": "..."}`；
  - T-OBS-ROUTE-TRACE-FALLBACK-01~03 全部通过；
  - 全量测试 ≥1580 passed，0 failed。
- 关联需求/测试：FR-OBS-010；BUG-BE-001；T-OBS-ROUTE-TRACE-FALLBACK-01~03。

### TASK-0194 M19-B 修复 Overview 页字段名对齐（BUG-FE-001 + WARN-FE-004）
- 状态：DONE
- 任务描述：
  修复前端 Overview 页两处字段名不匹配问题：
  1. **BUG-FE-001**：`Overview.jsx` 汇总统计行将 `data?.summary` 改为直接读取顶层字段 `data?.total / data?.up / data?.degraded / data?.down`，并将显示标签从 `healthy` 对齐为 `up`；
  2. **WARN-FE-004**：`ServiceCard.jsx` 组件 props 从 `name / latency / detail` 对齐为 `display_name / p95_ms / call_count`，同步更新 `Overview.jsx` 中传递给 `ServiceCard` 的属性映射。
  修复后重新构建前端产物（`npm run build`），更新 `static/monitor/` 下的构建产物。
- 输入：`monitor_ui/src/pages/Overview.jsx`；`monitor_ui/src/components/ServiceCard.jsx`；`host_brain/app/obs_routes.py`（确认 `ServicesHealthResponse` 字段）；审查报告 BUG-FE-001、WARN-FE-004。
- 输出：
  - `monitor_ui/src/pages/Overview.jsx`（字段读取修复）；
  - `monitor_ui/src/components/ServiceCard.jsx`（props 字段名对齐）；
  - `monitor_ui/dist/`（重新构建产物）；
  - `host_brain/app/static/monitor/`（更新静态文件）。
- 验收标准：
  - Overview 页顶部 4 个统计卡片（Total / Up / Degraded / Down）正确显示数值，不再显示 `—`；
  - ServiceCard 正确渲染 `display_name`、`p95_ms`、`call_count` 字段；
  - 前端构建无报错（`npm run build` 0 errors）；
  - 全量 Python 测试 ≥1580 passed，0 failed（前端修复不影响后端测试）。
- 关联需求/测试：FR-OBS-003；FR-OBS-008；BUG-FE-001；WARN-FE-004。

### TASK-0195 M19-C 修复 Trace API 路径 + Sessions 翻页闭包（WARN-FE-006 + WARN-FE-005）
- 状态：DONE
- 任务描述：
  修复前端两处功能性问题：
  1. **WARN-FE-006**：`monitor_ui/src/api/obs.js` 中 `fetchTraceNodes` 的调用路径从 `/obs/trace/${requestId}/nodes` 修正为 `/obs/trace/${requestId}`，与后端路由对齐；同步移除 Mock 分支中 `id.startsWith('mock')` 的特殊处理，改为统一的 `requestId === 'mock'` 开关；
  2. **WARN-FE-005**：`Sessions.jsx` 翻页逻辑中，将 `load` 函数的 `useCallback` 依赖从 `[page, ...]` 改为接受 `pageNum` 参数，翻页时调用 `load(page + 1)` 传入新页码，消除闭包陈旧值问题。
  修复后重新构建前端产物。
- 输入：`monitor_ui/src/api/obs.js`；`monitor_ui/src/pages/Sessions.jsx`；`monitor_ui/src/pages/Trace.jsx`；审查报告 WARN-FE-006、WARN-FE-005。
- 输出：
  - `monitor_ui/src/api/obs.js`（Trace API 路径修正）；
  - `monitor_ui/src/pages/Sessions.jsx`（翻页闭包修复）；
  - `monitor_ui/dist/`（重新构建产物）；
  - `host_brain/app/static/monitor/`（更新静态文件）。
- 验收标准：
  - `fetchTraceNodes` 调用路径为 `/obs/trace/{requestId}`（无 `/nodes` 后缀）；
  - Sessions 页翻页时请求的 `page` 参数与点击的页码一致（无闭包陈旧值）；
  - 前端构建无报错；
  - 全量 Python 测试 ≥1580 passed，0 failed。
- 关联需求/测试：FR-OBS-006；FR-OBS-009；WARN-FE-006；WARN-FE-005。

### TASK-0196 M19-D 重构 obs_routes.py 调用 Service 层（WARN-BE-001~003）
- 状态：DONE
- 任务描述：
  消除 `obs_routes.py` 与 Service 层的三组平行实现，将 Route 层改为调用已有 Service 层：
  1. **WARN-BE-001**：`GET /obs/services/health` 端点改为调用 `ServiceHealthAggregator.aggregate(side=side)`，删除路由层内联的聚合逻辑；
  2. **WARN-BE-002**：`GET /obs/sessions` 端点改为调用 `SessionQueryService.query(filters, page, page_size)`，删除路由层内联的查询逻辑；
  3. **WARN-BE-003**：`GET /obs/metrics/timeseries` 端点改为调用 `MetricsTimeseriesService.query(metrics, interval, start_time, end_time)`，删除路由层内联的聚合逻辑。
  重构后确保响应结构与原有结构兼容（前端无需修改），并补充必要的依赖注入（通过 `request.app.state` 或参数传递 `ledger` 实例）。
- 输入：`host_brain/app/obs_routes.py`；`host_brain/obs/service_health_aggregator.py`；`host_brain/obs/session_query.py`；`host_brain/obs/metrics_timeseries.py`；审查报告 WARN-BE-001~003。
- 输出：
  - `host_brain/app/obs_routes.py`（三个端点重构为调用 Service 层，删除内联逻辑）；
  - `host_brain/obs/service_health_aggregator.py`（如需调整接口签名以适配路由层调用）；
  - `host_brain/obs/session_query.py`（如需调整接口签名）；
  - `host_brain/obs/metrics_timeseries.py`（如需调整接口签名）。
- 验收标准：
  - `obs_routes.py` 三个端点不再包含内联业务逻辑，仅做参数解析 + Service 调用 + 响应序列化；
  - 原有 T-OBS-HEALTH-01~06、T-OBS-SESS-01~06、T-OBS-TS-01~06 全部继续通过；
  - 响应结构与重构前兼容（字段名、类型不变）；
  - 全量测试 ≥1580 passed，0 failed。
- 关联需求/测试：FR-OBS-008~011；WARN-BE-001；WARN-BE-002；WARN-BE-003；NFR-MAINT-001。

### TASK-0197 M19-E 修复 TraceNodeBuilder 全量加载 + 并行分支推断（BUG-BE-002 + WARN-BE-005）
- 状态：DONE
- 任务描述：
  修复 `trace_node_builder.py` 的两个核心问题：
  1. **BUG-BE-002**：`_fetch_intent_nodes`、`_fetch_governor_nodes`、`_fetch_skill_nodes`、`_fetch_voice_nodes` 四个方法改为按 `request_id` 直接过滤查询（在 SQL 层过滤而非全量加载后内存过滤）。需在 `SQLiteLedger` 中新增 `fetch_by_request_id(table, request_id)` 方法（或扩展现有 `fetch_by_time_range` 支持 `request_id` 过滤参数），并在 `sqlite_ledger.py` 中为相关表的 `request_id` 列补充索引（migration 文件）；
  2. **WARN-BE-005**：`_infer_parent_ids` 改为基于 `node_type` 的树形推断：`intent` 节点无 parent；`governor` 节点 parent 为 `intent`；`skill` / `llm` 节点 parent 为 `governor`；`asr` / `tts` / `vts` 节点 parent 为对应的 `skill` 节点（按 `start_ts_ms` 最近匹配）；支持多个节点共享同一 parent（并行分支）。
- 输入：`host_brain/obs/trace_node_builder.py`；`host_brain/audit/sqlite_ledger.py`；审查报告 BUG-BE-002、WARN-BE-005；DevSpec §29.6.1。
- 输出：
  - `host_brain/obs/trace_node_builder.py`（SQL 级过滤 + 树形 parent 推断）；
  - `host_brain/audit/sqlite_ledger.py`（新增 `fetch_by_request_id` 方法）；
  - `host_brain/audit/migrations/0020_trace_request_id_index.sql`（request_id 索引）；
  - `tests/unit/test_trace_node_builder.py`（更新 T-OBS-TRACE-03 断言，补充并行分支测试用例 T-OBS-TRACE-09~10）。
- 验收标准：
  - `_fetch_*_nodes` 方法不再拉取全量数据，仅返回指定 `request_id` 的行；
  - 并行分支场景（两个 skill 节点共享同一 governor parent）正确推断 parent_node_id；
  - T-OBS-TRACE-01~10 全部通过；
  - 全量测试 ≥1582 passed，0 failed。
- 关联需求/测试：FR-OBS-010；BUG-BE-002；WARN-BE-005；T-OBS-TRACE-01~10。

### TASK-0198 M19-F 补充 obs_routes.py 集成测试（WARN-TEST-001~002）
- 状态：DONE
- 任务描述：
  补充两类测试覆盖缺口：
  1. **WARN-TEST-002**：为 `obs_routes.py` 的 4 个端点新增集成测试（使用 FastAPI `TestClient`），覆盖正常路径、参数过滤、边界条件（空结果、不存在 ID、参数缺失）；
  2. **WARN-TEST-001**：更新 `test_trace_node_builder.py` 中 T-OBS-TRACE-03，补充并行分支断言（两个节点共享同一 parent_node_id）；新增 T-OBS-TRACE-09（并行 skill 节点 parent 均为 governor）和 T-OBS-TRACE-10（ASR/TTS 节点 parent 为对应 skill 节点）。
  集成测试使用内存 SQLite（`:memory:`）+ 预置 fixture 数据，不依赖真实服务。
- 输入：`host_brain/app/obs_routes.py`（M19-D 重构后）；`host_brain/obs/`（M19-E 修复后）；`tests/unit/test_trace_node_builder.py`；审查报告 WARN-TEST-001~002。
- 输出：
  - `tests/integration/test_obs_routes_integration.py`（T-OBS-ROUTE-INT-01~16：4 个端点 × 4 个场景）；
  - `tests/unit/test_trace_node_builder.py`（更新 T-OBS-TRACE-03，新增 T-OBS-TRACE-09~10）。
- 验收标准：
  - T-OBS-ROUTE-INT-01~16 全部通过；
  - T-OBS-TRACE-03、09、10 全部通过；
  - 全量测试 ≥1598 passed，0 failed；
  - `obs_routes.py` 4 个端点的集成测试覆盖率 ≥80%。
- 关联需求/测试：FR-OBS-008~011；WARN-TEST-001；WARN-TEST-002；T-OBS-ROUTE-INT-01~16；T-OBS-TRACE-09~10。

### TASK-0199 M19-G M19 发布清单
- 状态：DONE
- 任务描述：生成 M19 发布清单（m19_release.json），记录 M19-A~F 修复文件清单 + 测试通过数 + 质量门禁；同步更新代码审查报告（full_code_review_report_2026-03-04-m17m18.md）中各修复项状态为 FIXED；更新 risk_registry.md 中对应风险条目状态为 MITIGATED。
- 输入：TASK-0193~0198 全部 DONE。
- 输出：
  - `docs/50_release/m19_release.json`；
  - `docs/40_agents/full_code_review_report_2026-03-04-m17m18.md`（修复项状态更新）；
  - `docs/60_trace/risk_registry.md`（P0/P1 风险条目状态更新为 MITIGATED）。
- 验收标准：
  - release_ready=true；
  - all_p0_bugs_fixed=true（BUG-BE-001、BUG-FE-001 均已修复）；
  - all_p1_issues_resolved=true；
  - zero_regressions=true；
  - total_tests_passed ≥ 1598。
- 关联需求：FR-OBS-003~011；NFR-REL-001；NFR-MAINT-001；BUG-BE-001；BUG-BE-002；BUG-FE-001。

### TASK-0200 M20-A：AgentHealthCheck — SchedulerEngine 心跳精神状态检测
- 状态：DONE
- 任务描述：在 `SchedulerEngine` 中注册 `agent_health_check` 定时任务（默认间隔 30 分钟），向 LLM 节点发送 Heartbeat Prompt，依据回复内容判断 Agent 精神状态；回复含 `HEARTBEAT_OK` 则静默丢弃，否则推送 `HEARTBEAT_ALERT` 事件至审计 Ledger 并写入可观测日志。
- 输入：
  - `host_brain/scheduler/scheduler.py`（SchedulerEngine 现有实现）；
  - `host_brain/audit/sqlite_ledger.py`（审计写入接口）；
  - DevSpec §21.4（SchedulerEngine 接口契约）；
  - FR-OBS-001（关键指标与面板）。
- 输出：
  - `host_brain/scheduler/scheduler.py`：新增 `AgentHealthCheckTask` 任务工厂，含 Heartbeat Prompt 模板（可配置）、最大回复字符限制（默认 300）、静默过滤逻辑（`HEARTBEAT_OK` 丢弃）、异常推送逻辑（写入 `heartbeat_alert` 审计事件）；
  - `host_brain/scheduler/health_check_prompt.py`（新增）：Heartbeat Prompt 模板常量与 `parse_heartbeat_response(text) -> Literal["OK", "ALERT"]` 解析函数；
  - `host_brain/audit/sqlite_ledger.py`：新增 `heartbeat_events` 表迁移引用（或复用现有 `scheduler_events` 表，增加 `event_type=HEARTBEAT_ALERT` 枚举值）；
  - `tests/unit/test_agent_health_check.py`（新增）：T-HB-01~05（正常回复静默、异常回复推送、自定义 Prompt、字符截断、LLM 不可用降级）。
- 验收标准：
  - T-HB-01：回复含 `HEARTBEAT_OK` 时，`heartbeat_events` 表无新增记录，且不触发任何推送；
  - T-HB-02：回复不含 `HEARTBEAT_OK` 时，`heartbeat_events` 表写入一条 `event_type=HEARTBEAT_ALERT` 记录，含 `ts_ms`、`raw_response`（截断至 300 字符）、`task_id`；
  - T-HB-03：LLM 不可用（超时/异常）时，降级写入 `HEARTBEAT_ALERT`，`raw_response` 为 `"LLM_UNAVAILABLE"`，不抛出未捕获异常；
  - T-HB-04：`parse_heartbeat_response` 对含 `HEARTBEAT_OK` 的任意大小写变体均返回 `"OK"`；
  - T-HB-05：任务注册后可通过 `SchedulerEngine.list_health()` 查询到 `agent_health_check` 任务状态；
  - 全量测试 ≥1598 passed，0 failed（在 M19 基线上新增）。
- 涉及文件路径：
  - `host_brain/scheduler/scheduler.py`；
  - `host_brain/scheduler/health_check_prompt.py`（新增）；
  - `host_brain/audit/sqlite_ledger.py`；
  - `tests/unit/test_agent_health_check.py`（新增）。
- 关联需求/测试：FR-OBS-001；FR-OBS-008；T-HB-01~05。

### TASK-0201 M20-B：ContextCompressor 压缩前静默保存（COMPRESS_BEFORE_SAVE）
- 状态：DONE
- 任务描述：在 `ContextCompressor` 触发压缩（`summary` / `hybrid` 策略）前，先将当前对话中的重要信息静默写入 Episodic 记忆库，防止压缩操作导致关键上下文永久丢失；并在审计字段中增加 `COMPRESS_BEFORE_SAVE` 事件记录，标记压缩前保存的时机与内容摘要。
- 输入：
  - `host_brain/memory/context_compressor.py`（ContextCompressor 现有实现）；
  - `host_brain/memory/episodic.py`（EpisodicStore 写入接口）；
  - `host_brain/audit/sqlite_ledger.py`（审计写入接口）；
  - DevSpec §24.2.2（ContextCompressor 接口）；
  - FR-MEM-005（召回融合输出必须为证据包）。
- 输出：
  - `host_brain/memory/context_compressor.py`：在 `compress()` 方法中，当策略为 `summary` 或 `hybrid` 且消息数超过压缩阈值时，先调用 `_pre_compress_save(messages, episodic_writer)` 提取并写入重要片段（含 `evidence_refs`），再执行压缩；新增 `pre_compress_save_enabled: bool`（默认 `True`）配置项；
  - `host_brain/audit/sqlite_ledger.py`：新增 `COMPRESS_BEFORE_SAVE` 审计事件类型（或复用现有 `memory_events` 表，增加 `event_type` 枚举值），记录字段：`ts_ms`、`session_id`、`saved_count`（保存片段数）、`strategy`（压缩策略名）；
  - `tests/unit/test_context_compressor.py`：新增 T-CTX-06~08（压缩前保存触发、保存内容含 evidence_refs、pre_compress_save_enabled=False 时跳过保存）。
- 验收标准：
  - T-CTX-06：`summary` 策略触发压缩时，`_pre_compress_save` 被调用，Episodic 写入至少 1 条记录，且该记录含有效 `evidence_refs`；
  - T-CTX-07：审计 Ledger 中存在 `event_type=COMPRESS_BEFORE_SAVE` 记录，含 `session_id`、`saved_count ≥ 1`、`strategy="summary"`；
  - T-CTX-08：`pre_compress_save_enabled=False` 时，`_pre_compress_save` 不被调用，Episodic 无新增记录；
  - T-CTX-09：`sliding_window` 策略不触发 `_pre_compress_save`（仅截断，无需保存）；
  - 原有 T-CTX-01~05 全部继续通过；
  - 全量测试 ≥1603 passed，0 failed。
- 涉及文件路径：
  - `host_brain/memory/context_compressor.py`；
  - `host_brain/memory/episodic.py`（只读接口，不修改，仅调用）；
  - `host_brain/audit/sqlite_ledger.py`；
  - `tests/unit/test_context_compressor.py`。
- 关联需求/测试：FR-MEM-005；T-CTX-01~09。

### TASK-0202 M20-C：Episodic 混合检索增强（向量 70% + 全文 BM25/FTS5 30%）
- 状态：DONE
- 任务描述：在 `recall_fusion.py` 的 Episodic 检索管线中叠加全文检索（SQLite FTS5 或 BM25 算法），与现有向量检索结果按权重融合（向量 70%、全文 30%，权重可配置），提升对专有名词、人名、地名等语义向量不擅长内容的召回率；同时处理跨块语义断裂问题（相邻分块合并策略）。
- 输入：
  - `host_brain/retrieval/recall_fusion.py`（现有向量检索融合实现）；
  - `host_brain/retrieval/pipeline.py`（检索管线）；
  - `host_brain/memory/episodic.py`（EpisodicStore，含 SQLite 存储层）；
  - `host_brain/audit/sqlite_ledger.py`（SQLite 连接复用）；
  - FR-MEM-005（召回融合输出必须为证据包）；
  - FR-MEM-003（文本嵌入服务必须存在且可降级）。
- 输出：
  - `host_brain/retrieval/recall_fusion.py`：新增 `FullTextSearcher` 类（基于 SQLite FTS5 虚拟表）+ `HybridFusionStrategy`（向量分数 × 0.7 + 全文分数 × 0.3，权重通过 `vector_weight` / `fts_weight` 参数可配置）；融合后结果去重（按 `doc_id`），保留最高分；
  - `host_brain/audit/migrations/0021_episodic_fts5.sql`（新增）：为 Episodic 记忆表的 `content` 字段创建 FTS5 虚拟表（`episodic_fts`），支持中文分词（`unicode61` tokenizer）；
  - `host_brain/retrieval/pipeline.py`：`RetrievalPipeline` 新增 `use_hybrid: bool`（默认 `True`）配置项，`False` 时退化为纯向量检索（向后兼容）；
  - `tests/unit/test_recall_fusion_hybrid.py`（新增）：T-RCL-HYB-01~06（纯向量、纯全文、混合融合、权重配置、去重、FTS5 不可用降级为纯向量）。
- 验收标准：
  - T-RCL-HYB-01：混合检索对专有名词查询（如人名"夏目"）的召回结果中，全文检索命中条目的最终分数 ≥ 纯向量检索同条目分数（体现全文权重贡献）；
  - T-RCL-HYB-02：`use_hybrid=False` 时，`FullTextSearcher` 不被调用，结果与原有纯向量检索一致（向后兼容）；
  - T-RCL-HYB-03：同一 `doc_id` 在向量和全文结果中均命中时，融合后只保留一条，分数为加权融合值；
  - T-RCL-HYB-04：FTS5 虚拟表不可用（迁移未执行）时，自动降级为纯向量检索，不抛出未捕获异常；
  - T-RCL-HYB-05：`vector_weight + fts_weight` 不等于 1.0 时，抛出 `ValueError`（配置校验）；
  - T-RCL-HYB-06：融合结果仍为合法 `EvidencePack`（含 `doc_id`、`score`、`source_ref`、`content`）；
  - 原有 Episodic 检索相关测试全部继续通过；
  - 全量测试 ≥1609 passed，0 failed。
- 涉及文件路径：
  - `host_brain/retrieval/recall_fusion.py`；
  - `host_brain/retrieval/pipeline.py`；
  - `host_brain/memory/episodic.py`（只读接口，不修改）；
  - `host_brain/audit/migrations/0021_episodic_fts5.sql`（新增）；
  - `tests/unit/test_recall_fusion_hybrid.py`（新增）。
- 关联需求/测试：FR-MEM-003；FR-MEM-005；T-RCL-HYB-01~06。



