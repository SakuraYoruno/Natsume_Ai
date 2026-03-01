受众：CODEX
标题：DevTasks（任务清单：唯一可执行任务来源）

版本：v1.3
日期：2026-02-28

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
  - Host 配置对象（`NATSUME_ENV`、服务开关、降级开关、超时/重试口径）；
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
