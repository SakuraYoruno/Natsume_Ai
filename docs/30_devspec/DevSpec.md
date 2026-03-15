受众：CODEX
标题：开发规格 DevSpec（从零可重建：目录骨架与契约落点）

本文只写“怎么做”与“如何验证”，不写产品愿景。

# Natsume Ai 产品开发文档（Dev Spec）
版本：v1.6
日期：2026-03-04
适用架构：Host_Brain（Python，含 LangGraph 编排） + Natsume_Body（Edge） + Cloud Exocortex + ClawdBot

本文件用于把白皮书与 PRD 的原则/需求落成可实现的工程规格：模块边界、接口、Schema、日志、测试与里程碑。
目标：即使仓库为空，也能按本文档给出的“目录骨架 + 契约 + 里程碑交付物”，从零重新开发到最小可运行闭环。

---

## 0. 术语与硬约束（执行口径）

0.1 单向链：语义内核（LLM 草案） → Host Governor → Skills/Actuators → Audit
0.2 事实：仅以审计链（intent/governor/skill_exec）与硬状态/SSOT 为准。
0.3 任何执行必须携带 approval_token；高风险必须 confirm_token；可逆变更默认要求 undo_token+manifest。
0.4 任一 LLM 节点永远不执行、不裁决、不宣称已执行；所有“已完成/已保存/已确认”只能引用 Host 审计摘要字段。

0.5 文档关系与追溯口径（DevSpec 必须遵守）
DevSpec 的上游是 PRD 的 FR-需求编号；下游是 DevTasks 的 TASK-任务编号与 tests 的 T-编号。

强制追溯链：
FR-…（PRD：做什么+验收）
→ DevSpec 章节（怎么做+如何验证）
→ T-…（测试编号：断言点/日志点/契约点）
→ TASK-…（任务卡：最小交付单元，引用 FR/T/章节）

DevSpec 写作规则（工程契约级）：
1）每个可实现的规格条目必须声明“覆盖的 FR 编号（1+）”。
2）每个关键契约点必须分配“测试编号 T-…”，并写清楚验证口径（输入/输出/审计字段/拒绝码）。
3）任何会产生写入（审计/关系/记忆/Canon/Vault）的动作，必须写明 evidence_refs 的来源与记录位置（Ledger 表）。
4）DevTasks 的拆分来源只能是 DevSpec：任务卡不允许直接引用白皮书/PRD 来臆造实现细节。

---

## 1. 代码仓库结构（从零可重建完整版）

本节是“唯一目录真相”。任何新模块、脚本与资产必须落到这里的某个目录下，并满足：
- 单写者原则：SSOT 存储的写入能力只能出现在指定模块；
- 分槽隔离：facts / world_canon / style_canon / episodic / artifacts 不混用；
- 可审计：所有重要写入都能指回 SQLite Ledger 的 evidence_refs；
- 可回放：最小链路能按审计日志复现关键决策与执行结果。

仓库单一 mono-repo（Host 侧生成两端代码并版本化；Edge 拉取同一仓库）：

### 1.1 根目录（Repo Root）

约束：目录树是“唯一真相”。任何新增模块必须先在此登记；任何契约资产（Flow/ModelPlane/Contracts）必须有稳定落点。

```aiignore
/
├─ README.md
├─ LICENSE
├─ .editorconfig
├─ .gitattributes
├─ .gitignore
├─ .pre-commit-config.yaml # 文档受众门禁/格式门禁（不在本文描述命令）
├─ configs/
│  └─ model_plane/
│     ├─ node_registry.yaml
│     └─ model_profiles.yaml
├─ prompts/
│  ├─ CODEX_PROMPT.md # Codex 入口提示词（输出格式/行为约束）
│  ├─ PROJECT_PROMPT.md # Project 入口提示词（只写文档、不改代码）
│  └─ llm_contract/
│     ├─ core_output_contract.json # LLM→Host Strict JSON 输出契约示例（结构示例，不是实现）
│     └─ refusal_templates.md # 统一拒绝叙事范式（非事实）
├─ docs/
│  ├─ 00_DOCMAP.md
│  ├─ 10_whitepaper/
│  │  └─ whitepaper_constitution_persona.md
│  ├─ 15_theory/
│  │  └─ Theory.md
│  ├─ 20_prd/
│  │  └─ PRD.md
│  ├─ 30_devspec/
│  │  ├─ DevSpec.md
│  │  ├─ DevTasks.md
│  │  └─ ModelPlane.md
│  ├─ 40_agents/
│  │  └─ AGENTS.md
│  ├─ 50_project/
│  │  ├─ PROJECT_BRIEF.md
│  │  ├─ PROJECT_TASKS.md
│  │  └─ WORKSHEET_Project_Codex_Git_Flow.md
│  └─ 60_trace/
│     ├─ requirements_trace.md
│     └─ requirements_trace.csv
├─ flows/
│  ├─ exports/ # Flow 导出物归档（图/配置原件）
│  └─ active.pointer # 当前生效版本指针（dev/staging/prod → exports 内路径）
├─ host_brain/
│  ├─ pyproject.toml # 或 requirements*；必须可复现依赖版本
│  ├─ app/
│  │  ├─ main.py # HTTP API 入口（健康检查/链路入口）
│  │  └─ config.py # 配置加载（分层：defaults/local/secret）
│  ├─ core/
│  │  ├─ context_pack.py # Host→LLM 输入打包（分槽隔离）
│  │  ├─ qa.py # 输出合规：Strict JSON/伪执行/Canon-as-fact
│  │  ├─ metabolism.py # energy/mood/sys_mode 演化（硬状态）
│  │  ├─ ids.py # intent_id/event_id/request_id 生成规则
│  │  └─ errors.py # reason_code 与错误对象（稳定枚举）
│  ├─ governor/
│  │  ├─ schemas.py # Intent/GovernorOutput/Token/Manifest（Schema/验证器）
│  │  ├─ rules.py # R1–R7 Gate（trust/energy/sys_mode/scope/safety/airlock）
│  │  ├─ decision.py # 裁决入口：输入→校验→决策→写审计
│  │  └─ tokens.py # approval/confirm/undo 票据绑定与校验规则
│  ├─ skills/
│  │  ├─ registry.py # SkillMeta 注册（clearance/risk/scope/reversible）
│  │  ├─ loader.py # 唯一合法执行入口：token 校验、幂等、审计写入
│  │  └─ impl/ # 具体技能实现（只允许 loader import）
│  ├─ tools/
│  │  ├─ catalog.py # ToolMeta SSOT（仅元数据）
│  │  ├─ selector.py # Top-K 工具候选选择（tool_meta_pack）
│  │  ├─ broker.py # ToolBroker：工具执行单入口（token+审计）
│  │  └─ mcp_adapter.py # MCP provider→ToolMeta 映射与治理入口
│  ├─ audit/
│  │  ├─ sqlite_ledger.py # Ledger SSOT：intent/governor/skill_exec + 索引
│  │  ├─ migrations/ # SQLite 表结构迁移描述（不可手写漂移）
│  │  └─ retention.py # 保留/归档/GC 策略（动作必须可审计）
│  ├─ bond/
│  │  ├─ neo4j_client.py
│  │  ├─ bond_dynamics.py # 唯一写 Neo4j（Bond SSOT 单写者）
│  │  ├─ summarizer.py # Bond Summary 派生（不得写回 SSOT）
│  │  └─ migrations/ # Neo4j 约束/索引描述
│  ├─ memory/
│  │  ├─ episodic.py # 向量库写入/召回（必须 evidence_refs）
│  │  ├─ chroma_client.py # 向量库客户端封装（写入仅 episodic.py）
│  │  ├─ canon_style_store.py # Style Canon SSOT（版本化、tone_only）
│  │  ├─ canon_world_store.py # World Canon SSOT（最小权威内容集合）
│  │  └─ canon_sync.py # Canon 扫描/索引同步（写入需留痕）
│  ├─ canon/
│  │  ├─ style/ # Style Canon 资产（模板/范式，非事实）
│  │  └─ world/ # World Canon 条目资产（设定/术语/不变量）
│  ├─ lsp/
│  │  ├─ vault.py # Artifact Vault：manifest/undo/GC（单写者）
│  │  └─ policies/ # Vault policy profiles（保留/脱敏/生命周期）
│  ├─ airlock/
│  │  ├─ sanitizer.py # 出站脱敏报告生成（必须可审计）
│  │  └─ quarantine.py # 入站隔离区（不得直通执行）
│  ├─ channels/
│  │  ├─ wechat.py # 微信 I/O 事件化与审计串联
│  │  └─ vts.py # emotion_tag→expression_id 固定映射与 talk gate
│  └─ integrations/
│     └─ llm_gateway.py # LLM 调用封装（blocking/streaming 统一入口）
├─ edge_client/
│  ├─ pyproject.toml
│  ├─ senses/ # ears/eyes：采集与摘要（不裁决）
│  ├─ actuators/ # mouth/speaker/avatar：执行器（不裁决）
│  ├─ rpc/ # Host↔Edge RPC（只接受带票据的执行请求）
│  ├─ proofs/ # before/after 证据采集（写入 Vault 的原始材料来源）
│  └─ config.py
├─ tests/
│  ├─ contract/ # Schema/validator/gates/静态扫描（快速、无外部依赖优先）
│  ├─ e2e/ # 最小链路：Host→LLM stub→Governor→Skill stub→Audit
│  ├─ fixtures/ # intents、坏输出、日志回放样例
│  └─ TESTPLAN.md # 只列 T-xx：用例→断言点→输入输出（不写命令）
├─ tools/
│  ├─ static_scan/ # 单写者/旁路调用/受众门禁扫描规则
│  ├─ doc_lint/ # 文档头部受众/标题校验规则
│  └─ replay/ # 审计回放与最小复现工具（只读）
└─ .github/
   └─ workflows/ # CI 定义（只描述应检查什么，不在本文给命令）
```

### 1.2 从零重建的最小基线（M0–M3）
目的：保证“空仓库也能按阶段交付”。每阶段都必须具备可审计、可验证的产物。

M0：骨架可解析（不接外部依赖也能闭环）
交付物：
- Intent Schema（最小字段）+ GovernorOutput Schema（decision/reason_code/rule_trace）
- SQLite Ledger（intent_log/governor_log/skill_exec_log 最小三联表）
- LLM stub（或固定样例输入输出）可驱动 Host 完整链路

验证点：
- 任意输入最终输出保持 Strict JSON 可解析；
- 拒绝必带 reason_code；
- skill_exec 只在 APPROVE 且 token 合法时出现。

M1：Governor 可治理（门禁可回归）
交付物：
- R1–R7 Gate 的规则骨架（即使阈值未调优也必须可观测）
- approval/confirm/undo 的绑定字段与校验逻辑（含幂等）

验证点：
- 同一路径同版本下 reason_code 稳定；
- 缺票据/越权 scope/禁用 sys_mode 触发明确拒绝并写审计。

M2：六库分工落地（写入边界可静态验证）
交付物：
- bond_dynamics（Neo4j 单写者）+ bond_update_log 留痕
- episodic（向量库单写者）+ memory_ops_log 生命周期留痕
- canon_style_store / canon_world_store（版本化、变更留痕）
- vault（manifest/undo/GC 约束与审计）

验证点：
- 任意非单写者模块无法写入对应 SSOT（可由静态扫描报告证明）；
- episodic/world/style 的注入分槽隔离且不会被 QA 误当事实。

M3：Edge 联调（证据材料可回放）
交付物：
- Host↔Edge RPC 契约：只接受带票据的执行请求
- proofs→Vault：before/after 证据能形成 manifest 串联到 intent_id

验证点：
- UI/视觉相关能力在证据缺失时必须降级/拒绝；
- Vault GC 不破坏 undo 窗口（策略可审计）。

### 1.2.1 冷启动策略（NoKey/Service Unavailable：服务必须存在，允许暂不可用）
目标：在“无云端 API Key / 任一服务暂不可用”的条件下，仍能交付最小闭环：输出可解析、审计可回放、无伪执行宣告。

A）交付边界（项目范围口径）
- 所有服务项为“必须实现、缺一不可”（见 PRD 2.3）。
- 允许存在“运行时暂不可用/未配置/降级”的情况，但必须：
  - 有稳定接口与健康状态；
  - 降级时输出结构稳定且 reason_code/stage 可统计；
  - 不得把“不可用/未触发”伪装为“事实为空”。

B）冷启动最低形态（从零可重建）
- SQLite Ledger：必须可写入最小三联画；缺失则无法称为可回放系统。
- Neo4j Bond SSOT / Episodic 向量库：必须存在对应的连接层与健康状态；不可用时对外只输出降级口径（空/默认），并记录 stage/reason_code。
- Style/World Canon / Vault：必须以版本化资产形态存在；可为空但不得导致系统伪造设定或伪造事实。
- Cloud Exocortex：必须存在 gateway + airlock；无 key 时必须进入“外脑不可用”降级路径，且不影响结构稳定。
- Edge（语音/外显）：必须存在 RPC 与能力端点；不可用时 Host 进入“无身体模式”降级路径，仍保持结构稳定与审计可回放。

C）可观测（必须）
- 任一服务不可用不得导致崩溃；必须输出可解析结构，并在 quality.stage 标注失败发生的阶段。
- 同类缺失在同版本下 reason_code 稳定可统计。
- 任何 LLM（含 Edge 兜底模型）输出仍必须满足 Strict JSON 契约，并受 QA（伪执行、Canon-as-fact、格式漂移）约束。

### 1.3 必须不进仓库的内容（从零重建的可复现前提）
- 任何 secret（API key、token、个人账号凭据）：只能以“模板文件 + 本地覆盖”的方式存在。
- artifacts（二进制截图/音频等）：默认不进 Git，必须通过 Vault manifest 可追溯其来源与生命周期。
- local overrides（本地路径、设备 ID）：必须独立于可复现配置层，不得写死在代码与文档正文中。

### 1.4 目录职责红线（从零重建时最易踩坑）
- flows/ 只存 Flow 导出物与指针；不存“事实源”与“执行脚本”。
- host_brain/audit/ 是“发生过什么”的索引枢纽；任何写入 SSOT 的动作必须能指回它。
- host_brain/canon/world/ 的条目可被引用用于一致性，但不得用于宣告已执行；此类宣告只认审计链。
- tests/contract 负责契约稳定性；tests/e2e 负责最小闭环；两者都不应依赖真实外部环境才能跑通其断言逻辑（通过 stub/fixtures 达成）。

### 1.5 配置资产：Flow 资产（LangGraph 图/配置）与指针契约（可部署 SSOT）
定位：Flow 资产属于“可部署配置资产”，其行为变化必须可版本化、可回滚、可追溯；但它不承载执行事实，执行事实仍以审计链为准。

目录与文件（语义固定）：
- 导出物归档目录：flows/exports/（仅保存 Flow 导出物原件）
- 当前生效指针：flows/active.pointer（必须为单个合法 JSON 对象）
- 审计索引（文档层）：docs/60_trace/flows/index.md（按环境给出“当前生效版本”与变更摘要）

active.pointer 格式（必须）：
- 结构：按环境映射到 exports 内的相对路径（或文件名），键集合固定为 dev/staging/prod。
- 约束：
  - 指向的文件必须存在于 flows/exports/ 下；
  - 任一环境缺失键或指向越界（不在 exports 下）视为配置不合规；
  - docs/60_trace/flows/index.md 的“当前生效版本”必须与 active.pointer 一致（允许 index 额外记录导出时间/摘要，但不允许指向不同文件）。

最小回归测试编号（配置资产）：
- T-Flow-01：active.pointer 合法性与指针存在性（结构/键集合/路径范围/目标文件存在）
- T-Flow-02：Trace 索引一致性（index.md 的按环境生效版本与 active.pointer 完全一致）

---

## 2. 运行时进程拓扑与通信

## 2. 运行时进程拓扑与通信

2.1 Host 进程（主权裁决与统一编排）
- 对外提供：`POST /v1/chat`（用户输入 → 输出 narrative（rendered_ja） + intent_draft + quality；可选 debug.core_narrative_zh）
- 统一编排：所有 Agent 与链路编排仅在 Host 发生；Edge 端组件仅作为“远程能力提供者”被调用（draft-only，不具终审权）。
- 必备内部服务（必须实现）：Governor/Token/Audit、SQLite Ledger、Neo4j Bond SSOT、Episodic 向量库、Embedding、Reranker、Recall/Fusion、Screen Capture、OCR/Vision、ClawdBot、Cloud Gateway + AIRLOCK、Observability。
- 运行时不可用：任一服务不可用必须可观测降级并保持输出结构稳定（不得伪执行）。

2.2 Edge 进程（身体能力：语音链与外显）
- 对外提供（给 Host）：RPC 能力端点（示例命名，具体路径以 edge_client/rpc 统一注册表为准）：
  - asr：上送转写事件（asr_chunk：text/timestamps/confidence/alternates 可选）
  - speak：接收 Host 的最终文本并完成 TTS+播放（支持 barge-in 打断）
  - vts：talk gate 与 emotion_tag→expression_id 固定映射触发
  - fallback_llm：兜底草案能力（默认卸载、低频触发、draft-only）
  - health：返回 Edge 能力矩阵与当前资源状态（含 GPU 显存与队列状态）
- 资源闸门（必须）：Edge 侧必须实施任务优先级与互斥策略，防止 ASR/TTS/兜底LLM 高峰叠加导致不稳定；优先级为 ASR>播放>TTS合成>兜底LLM。

2.3 Cloud Exocortex
- Host 调用外部模型：统一经 `integrations/llm_gateway.py` 出站（经 AIRLOCK 脱敏）
- 外部输出：严格 JSON（narrative + intent_draft 或渲染文本），一律降权为草案，不得直通执行

2.4 通信安全（最小实现）
- Host→Edge：每次请求带 `request_id` + `HMAC(signature)`（共享密钥）+ `ts_ms` + `nonce`
- Edge 维护 nonce 缓存窗口，拒绝重放（REPLAY_DETECTED）。

2.5 Edge 兜底 LLM 的接入方式（工具化，不作为 Agent）
- 定位：Edge 兜底 LLM 不是 Agent，不参与主链路裁决；仅提供 draft-only 能力：
  - presence（在场回应草案）
  - asr_compile（转写清洗与候选）
  - retrieval_hints（检索提示：rewrite/keywords/entities/constraints）
  - context_compress（滚动摘要）
- 输出约束：所有输出必须为单个合法 JSON；必须带 draft_only=true；不得包含决策字段（allow/deny/confirm_required 等）；不得包含任何执行事实宣告。
- 失败策略：Edge 兜底 LLM 不可用/加载失败必须快速失败并返回稳定错误码；Host 侧必须回退到规则/模板兜底，不阻塞语音主链。

---

## 3. Schema：Intent（必须强类型）

定位：Intent 是“语言→代码”的唯一边界对象，是 Host Governor 可裁决的请求对象。LLM 只能提出 intent_draft（草案）；Host 只接受强类型 Intent（允许宽松解析修语法，但禁止补语义）。

覆盖需求：FR-INTENT-001~003
回归口径：T-18~T-22

### 3.1 Intent 最小字段（固定 JSON 对象）
Host 只接受“单个 JSON 对象”，不接受数组/多段文本。最小字段如下（字段名语义固定）：
- schema_version：固定字符串（如 "Natsume.intent.v1"）
- id：全局唯一（ULID/UUID 均可；必须可审计回放）
- ts_ms：毫秒时间戳（用于过期窗）
- session_id：会话标识
- seq：会话内单调递增（防乱序/回滚）
- type：枚举（意图类别）
- action：枚举（Host allowlist）
- target：枚举（Host allowlist）
- args：object（按 action 强类型校验）
- nonce：随机串（防重放；需缓存窗口）

硬约束：
- type/action/target 必须 allowlist；任何未知值一律 REJECT（INTENT_SCHEMA_INVALID）。
- args 必须按 action 的 ArgsModel 校验；失败一律 REJECT（INTENT_ARGS_INVALID）。
- seq 必须单调递增；乱序/回滚一律 REJECT（SEQ_INVALID）。
- id/nonce 必须做重放保护；重复一律 REJECT（REPLAY_DETECTED）。
- ts_ms 超出过期窗口一律 REJECT（EXPIRED_INTENT）。

### 3.2 两阶段校验（必须）
阶段 A：lenient parse（只修语法，不补语义）
- 目标：把“可疑但可能是 JSON”变成“可解析 JSON 对象”，只做语法层修复。
- 允许：去掉前后缀噪声、修复多余逗号/引号等纯语法问题，使其成为单个 JSON 对象。
- 禁止：臆造 action/target/type/args；禁止“猜测用户意图”来补字段。
失败映射：INTENT_PARSE_FAILED

阶段 B：strict validate（强类型/枚举/参数契约/重放/时效）
- 字段/类型/枚举：失败→INTENT_SCHEMA_INVALID
- args：失败→INTENT_ARGS_INVALID
- 重放：失败→REPLAY_DETECTED
- seq：失败→SEQ_INVALID
- 过期：失败→EXPIRED_INTENT

### 3.3 args 参数契约（每个 action 一份）
每个 action 都必须在 schemas.py（或等价模块）中绑定 ArgsModel，并声明：
- required_keys / optional_keys
- 类型、范围、长度、正则
- 路径安全（如 base_dir 约束；禁止 `..`）
- 域名 allowlist / data_classification（配合 AIRLOCK）

### 3.4 审计字段（Intent 层必须写入）
Intent 进入 Governor 前，至少要在 intent_log（或等价审计表）记录：
- raw_text（原始草案/原始输入片段摘要）
- normalized_json（宽松解析后的 JSON）
- session_id / seq / ts_ms / nonce
- parse_stage：LENIENT_OK / LENIENT_FAIL
- lint_result：字段缺失/枚举越界/args 失败的结构化摘要（不得写成自然语言结论）

---

## 4. Schema：GovernorOutput（Host→上层）

定位：GovernorOutput 是 Host 对“是否允许执行”的对外稳定契约。它不是提示工程产物，必须可回放、可统计、可回归。

覆盖需求：FR-GOV-001~003、FR-CORE-002
回归口径：T-14~T-17（含 T-14/T-15）

### 4.1 输出字段（结构固定）
GovernorOutput 必须为单个 JSON 对象，字段语义固定：
- decision：APPROVE | REJECT
- reason_code：仅当 decision=REJECT 必须存在（稳定枚举）
- rule_trace：命中的 Gate 列表（R1–R7）+ 阈值摘要 + 快照摘要（可定位）
- tokens：
  - APPROVE：必须含 approval_token_ref（可选含 confirm_token_ref/undo_token_ref）
  - REJECT：不得下发 approval_token_ref
- snapshot_refs：hard_state/bond_snapshot（版本号或 hash）
- quality（建议）：stage（S0..S8）/ degraded（bool）/ notes（短枚举）

### 4.2 运行时断言（必须拦截）
- decision=REJECT 且缺 reason_code → 视为 GOVERNOR_OUTPUT_INVALID（不得对外透出“无 reason_code 的拒绝”）
- decision=REJECT 且缺 rule_trace → 同上（必须可定位命中门禁）
- decision=APPROVE 且缺 approval_token_ref → Skills/执行入口必须拒绝并记录 SKILL_TOKEN_MISSING

### 4.3 rule_trace 最小结构（建议固定字段）
每条 Gate 记录至少包含：
- gate_id：R1..R7
- hit：true/false
- threshold_summary：阈值摘要（不泄露敏感原值）
- snapshot_summary：输入快照摘要（桶化）
- reason_hint：短枚举（用于统计，不用自然语言）

---

## 5. Token：approval/confirm/undo（票据化执行）

定位：把“授权”从语言变成可验证数据结构。Skills/ToolBroker 只信 token，不信叙事。

覆盖需求：FR-TOKEN-001~003
回归口径：T-34~T-38

### 5.1 approval_token（短 TTL，可执行许可）
建议字段（语义固定）：
- token_id
- intent_id
- session_id
- seq
- skill_name（或 tool_id）
- issued_ts_ms / expiry_ts_ms
- nonce
- sig（签名）

绑定约束（必须）：
- token 必须绑定 intent_id + skill_name + session_id + seq
- 过期必须拒绝（TOKEN_INVALID）
- 串用（换 skill/换 intent/换会话）必须拒绝（TOKEN_INVALID）

### 5.2 confirm_token（高风险二次提交）
触发条件（至少覆盖）：
- risk_level=4 或 irreversible=true 或 scope 涉及 account/payment/system-settings/delete 等红线
规则：
- confirm_token 只能由 Host 确认流程生成；任何“口头确认/模型自述确认”无效。
- 缺 confirm_token 时必须 REJECT：CONFIRM_REQUIRED（不得进入执行路径）

### 5.3 undo_token + manifest（可撤销契约）
适用：
- reversible=true 的执行默认必须产出 undo_token_ref 与 manifest_ref
manifest 最小字段（语义固定）：
- manifest_id / intent_id / integrity_digest（sha256）
- items[]：before/after/path/hash/artifact_refs（可按能力裁剪）
- policy_profile_id / ts_ms

拒绝口径：
- reversible=true 但缺 manifest_ref → 视为不合规：UNDO_REQUIRED（或更精确的内部错误码；对外必须可统计）
- manifest digest 不匹配 → 撤销必须拒绝（TOKEN_INVALID 或 MANIFEST_INVALID 的稳定子类，二选一但必须固定）

---

## 6. Skills：注册、执行、幂等与审计

定位：Skills 是能力载体，只做执行不做裁决；唯一合法入口必须是 loader（或等价执行门），强制 token 校验、幂等与审计。

覆盖需求：FR-SKILL-001~003、FR-AUDIT-001
回归口径：T-15、T-34~T-38（以及后续 T-Skill-xx）

### 6.1 SkillMeta（强制字段，注册即校验）
每个 skill 必须声明并可静态扫描：
- name
- clearance_level（L0–L4）
- risk_level（0–4）
- reversible（true/false）
- scopes（process/filesystem/network/ui/camera/mic/account/payment…）
- requires_visual_audit（true/false）
- requires_confirm_token（true/false）
- data_classification（NONE/INTERNAL/EXTERNAL_KNOWLEDGE）
建议字段：
- rate_limit、allowlist、denylist、energy_cost、min_trust

注册期断言（必须）：
- 缺强制字段：注册失败（不得运行时“带病上线”）
- risk_level 与 requires_confirm_token 口径不一致：视为配置不合规（必须可审计）

### 6.2 唯一执行入口（强制）
执行入口（语义固定）：
- 输入：Intent + approval_token（可选 confirm_token/undo_token，按 SkillMeta 要求）
- 输出：SkillResult（结构化对象，必须可写入 skill_exec_log）

入口必须做的事（不可省略）：
1）token 校验：签名/过期/绑定一致
2）门禁复核：至少复核 scope/confirm_token 要求（与 Governor 决策一致）
3）幂等：同一 intent_id 不得产生第二次真实副作用（策略可“复用结果”或“拒绝重复”但必须固定）
4）审计写入：skill_exec_log 必须包含 intent_id、skill_name、approval_token_ref、result_digest、artifacts/manifest_ref/undo_ref（如适用）

### 6.3 幂等策略（必须可证明）
幂等证明口径（必须能被回放验证）：
- 重复 intent_id：不产生第二次执行事实
- 日志能显示“命中幂等”的原因（例如 reused_result / already_executed）

### 6.4 审计三联画串联键（必须一致）
- intent_log：提议（proposal）
- governor_log：裁决（decision）
- skill_exec_log：执行（fact）

串联键至少包含：
- request_id（或 turn_id）
- intent_id
- approval_token_ref（若执行发生）

---

## 7. Context Pack：Host→LLM 的输入契约
Host 打包 context_pack（JSON）给 LLM。核心目标：把“事实证据、权威内容、风格资产、召回线索”分槽隔离，防止混用导致越权叙事。

字段建议（分槽强隔离）：
- hard_snapshot：energy_bucket/mood_bucket/sys_mode/safety_bucket（不含敏感原值）
- bond_summary：stage/trend/capability_hint（不含具体 trust 数值）
- authoritative：
  - world_canon：[{doc_id,title,version,tags,excerpt,evidence_level}]
    - evidence_level 固定枚举：CANON_REFERENCE_ONLY（明确非执行事实）
- retrieval：
  - episodic：[{summary_cn,tags,ts,evidence_refs}]
- style_pack：
  - style_canon：[{canon_id,version,kind,triggers,template,constraints}]（tone_only=true, unsafe_as_fact=true）
- recent_events：[{kind,reason_code,ts,summary,evidence_refs}]
- constraints：
  - strict_json_output=true
  - no_false_execution_claim=true
  - no_canon_as_fact=true
  - tone_policy（短句/……/称呼）
  - intent_output_schema（强制输出 JSON 结构）

硬约束：
- world_canon、style_canon、episodic、recent_events 必须分字段隔离；任何混槽视为合规缺陷。
- LLM 不得自行检索，不得扩展召回；只消费 Host 给定 pack。
- 叙事引用 world_canon 必须使用“引用/设定口径”而非“执行事实口径”。

---

## 8. Data Stores：SQLite Ledger / Neo4j Bond SSOT / Episodic 向量库 / Style Canon / World Canon / Artifact Vault（具体实现契约）

定位：本章把“六库分工”写成可验证的工程契约：谁写谁读、SSOT 边界、Schema 口径、跨库引用与合规断言。任何偏离必须能被静态扫描或回归测试发现。

覆盖需求：FR-AUDIT-001~003、FR-BOND-001~003、FR-MEM-001~002、FR-CANON-001~002、FR-WCANON-001~003、FR-LSP-001~002、FR-AIRLOCK-001~002
回归口径：T-14/T-15、T-72~T-76、T-Canon-01~02、T-WCanon-01~03、T-Vault-01~02、T-39/T-40

### 8.0 责任分配（条目式，必须对齐）

本节是“六库分工”的工程契约口径：定义事实效力、SSOT 单写者、允许读取者、以及跨库最小引用键。
任何偏离必须能被静态扫描或回归测试捕获（见 8.8 与对应 T-xx）。

1）SQLite Ledger（审计账本与索引枢纽）
- 事实效力：最高（审计事实与回放索引）
- SSOT 单写者：host_brain/audit/sqlite_ledger.py
- 允许读取者：Host 全域（只读查询与回放）
- 最小跨库引用键：request_id、intent_id、event_id、evidence_refs

2）Neo4j（Bond 关系域 SSOT）
- 事实效力：关系结构域 SSOT（非执行事实）
- SSOT 单写者：host_brain/bond/bond_dynamics.py
- 允许读取者：Host（图查询/summary 派生；summarizer 不得写回）
- 最小跨库引用键：event_id + evidence_refs

3）Episodic 向量库（经历召回线索）
- 事实效力：无裁决权（相似性线索）
- SSOT 单写者：host_brain/memory/episodic.py
- 允许读取者：Host（召回；不得以此做裁决依据）
- 最小跨库引用键：memory_id + evidence_refs

4）Style Canon Store（风格资产：tone_only）
- 事实效力：仅口吻（不得提供事实）
- SSOT 单写者：host_brain/memory/canon_style_store.py
- 允许读取者：Host（注入 style_pack；不得混槽）
- 最小跨库引用键：canon_id + version + hash

5）World Canon Store（最小权威内容集合：可引用、非执行事实）
- 事实效力：权威内容引用（用于一致性与冲突裁决；非执行事实）
- SSOT 单写者：host_brain/memory/canon_world_store.py
- 允许读取者：Host（注入 authoritative.world_canon；不得混槽）
- 最小跨库引用键：doc_id + version + hash

6）Artifact Vault（证据/回滚材料）
- 事实效力：证据材料仓（支撑可回放与可撤销）
- SSOT 单写者：host_brain/lsp/vault.py
- 允许读取者：Host（引用/展示/回滚；GC 受策略约束）
- 最小跨库引用键：artifact_id + hash + manifest_id

### 8.1 SQLite Ledger（审计账本与索引枢纽）
最低三联画表（必须存在，语义固定）：
- intent_log（proposal）：记录“模型/规则引擎提出了什么 intent_draft”
- governor_log（decision）：记录“Host 如何裁决（APPROVE/REJECT + reason_code + rule_trace）”
- skill_exec_log（fact）：记录“执行实际发生了什么（或未发生）”

建议的最小字段口径（必须语义等价）：

A）intent_log
- request_id、intent_id、session_id、seq、ts_ms
- proposer_node_id（如 core_reasoner_primary / tool_planner / host_fallback_policy）
- model_snapshot（provider + model_id 或等价快照标识）
- prompt_profile_id（引用/哈希）
- tool_visibility_hash（本轮暴露的工具元数据子集哈希；无则为空）
- proposed_intent_draft_json（原文）
- lint_result_json（schema/args 校验摘要；禁止自然语言“结论句”替代）

B）governor_log
- intent_id、decision、reason_code（REJECT 必填）、rule_trace_json（必填）
- token_refs_json（APPROVE 必含 approval_token_ref）
- snapshot_refs_json（hard/bond snapshot 版本号或 hash）
- ts_ms、stage（S0..S8）

C）skill_exec_log
- intent_id、skill_name、approval_token_ref
- result_status/result_code、result_digest
- artifacts_json（可为空）、manifest_ref（可为空）、undo_ref（可为空）
- ts_ms

运行时断言（必须）：
- decision=REJECT → reason_code 非空且来自稳定枚举；缺失视为 GOVERNOR_OUTPUT_INVALID（不得对外透出）。
- 任意 skill_exec_log 行必须能关联 governor_log（同 intent_id）且 approval_token_ref 可核验。
- 任意写入类事件（Bond/Memory/Canon/Vault/Airlock/Clawd）都必须具备 evidence_refs（至少 1 个 intent_id 或 event_id 引用）。

### 8.2 Neo4j Bond SSOT（关系域唯一真相）
定位：Neo4j 只承载“关系结构与因果路径”，不得承载执行事实；执行事实仍以 Ledger 为准。

写入边界：
- 唯一写入者：bond/bond_dynamics.py
- summarizer.py 只能派生 summary，不得写回 SSOT

最小图模型（语义稳定，可扩展）：
- 节点：User、Session、Event、Topic、CapabilityHint
- 关系：HAS_EVENT / ABOUT / AFFECTS 等（具体命名可变，但必须可回放映射）
- Event 必须携带 event_id 与 evidence_refs（或可等价追溯到 SQLite 的 bond_update_log）

回归口径：
- 非 bond_dynamics 尝试写 Neo4j 必须被静态扫描/运行时断言拦截（T-SSOT-01）。
- Bond Summary 不得包含敏感数值（例如 trust=xx），只能 stage/trend/capability_hint（T-Bond-01）。

### 8.3 Episodic 向量库（经历召回线索，不具备裁决权）
定位：只用于相似性召回；不得当事实源。

写入边界：
- 唯一写入者：memory/episodic.py
- 必须 evidence_refs：无证据不得写入（防幻觉固化）

条目元数据最小字段（必须语义等价）：
- memory_id、ts_ms、text_hash、decay_profile_id
- evidence_refs（必须）
- merged_into（可空）、deleted_at（可空）
- visibility（internal/user_visible）
- embedding_model_id

生命周期（必须审计）：
- write/merge/decay/delete 必须写入 memory_ops_log（或等价表），并带 evidence_refs
- merge 必须保留“来源条目→目标条目”的可追溯关系

回归口径：
- T-Mem-01：无 evidence_refs 写入必失败且可解释
- T-Mem-02：merge/decay/delete 均可在 memory_ops_log 回放

8.3.1 RETRIEVAL（Embedding / Rerank / Evidence Pack）
覆盖需求：FR-MEM-003~005、FR-AUDIT-001、FR-CORE-002。

契约点（必须语义等价）：
- Embedding 接口必须存在：输入 text，输出 {embedding_vector, embedding_model_id, latency_ms, status}
- Reranker 接口必须存在：输入 {query, candidates[]}，输出 {scores[], rerank_model_id, latency_ms, status}
- Evidence Pack（融合输出）字段固定：{query, topk[], degraded?, stage, reason_code?}
  - topk[] 最小字段：{doc_id, source_ref, snippet, score, confidence, metadata?}

回归测试编号（新增，必须）：
- T-Ret-01：embedding 不可用时的降级口径（不得伪装为空事实）+ reason_code 稳定
- T-Ret-02：reranker 不可用时的降级口径 + evidence pack 结构仍稳定
- T-Ret-03：evidence pack 字段稳定 + 可回放引用（可用于回答与审计）

### 8.4 Style Canon Store（风格资产：tone_only）
定位：只决定“怎么说”，不得当事实注入。

写入边界：
- 唯一写入者：memory/canon_style_store.py（必须经白名单/Evolution 或等价流程）
- 任意变更必须写 canon_change_log（或等价表）

条目字段（最小）：
- canon_id、version、hash
- tone_only=true、unsafe_as_fact=true
- content（模板/范式）、tags、updated_by（proposal_id）、ts_ms

注入隔离：
- 只能进入 context_pack.style_pack；不得与 facts/world_canon/episodic 混槽（T-CP-01）。

### 8.5 World Canon Store（最小权威内容集合：可引用、非执行事实）
定位：稳定“内容一致性”，用于设定/术语/不变量引用与冲突裁决；不构成执行事实。

写入边界：
- 唯一写入者：memory/canon_world_store.py（必须经白名单/Evolution 或等价流程）
- 任意变更必须写 canon_change_log 与 canon_proposal_log（或语义等价）

推荐双层形态（本地优先、可审计）：
- 内容资产层（文件 SSOT）：host_brain/canon/world/ 下条目文件，至少包含 doc_id、version、title、tags、body
- 索引层（SQLite）：world_canon_doc（doc_id/version/hash/status/source_path/updated_ts）

同步契约：
- canon_sync 负责扫描文件→计算 hash→更新索引；任何变更必须写 canon_change_log（canon_kind=world）

引用口径（必须）：
- 输出中引用 world_canon 必须使用“设定/引用口径”，不得升级为“已执行/已保存/已确认”
- 任何完成性表述必须引用审计链摘要字段，否则 QA 拦截（与 9 章联动）

### 8.6 Artifact Vault（证据与可逆材料仓）
定位：保存截图/关键帧/脱敏报告/回滚材料等资产，支撑撤销与 UI 行为可回放。

写入边界：
- 唯一写入者：lsp/vault.py（经 Skill 执行产出）
- manifest 必须可索引到 intent_id，并能关联 undo_token_ref（若 reversible=true）

manifest 最小字段（必须语义等价）：
- manifest_id、intent_id、integrity_digest（sha256）
- items[]：{artifact_id, kind, hash, created_ts, evidence_refs}
- policy_profile_id、ts_ms
- undo_ref（可空，若 reversible=true 则必填）

GC 约束：
- 不得删除有效 undo 窗口材料
- 每次 GC 必须写 vault_gc_log（含 policy_profile_id、deleted_artifact_ids、evidence_refs）

### 8.7 Airlock（出站净化/入站隔离）与日志口径
出站必须产生：
- sanitization_report_json（removed_fields/redactions/payload_digest）
- airlock_log.direction=EGRESS，且含 request_id

入站必须隔离与分类：
- 分类枚举：FACT/DRAFT_CODE/DRAFT_INSTRUCTION/UNKNOWN
- 任何入站内容不得直通执行；必须转成 Intent 并过 Governor，否则 EXTERNAL_DATA_GATE

### 8.8 静态扫描断言（单写者/旁路禁止，必须可回归）
必须覆盖的禁止项（口径级）：
- 非 skills/loader 不得直接调用 skills/impl
- 非 bond/bond_dynamics 不得持有/调用 Neo4j 写入能力
- 非 memory/episodic 不得持有/调用向量库写入能力
- 非 memory/canon_style_store 不得持有/调用 Style Canon 写入能力
- 非 memory/canon_world_store 不得持有/调用 World Canon 写入能力
- 非 lsp/vault 不得持有/调用 Vault 写入能力

回归口径：
- T-SSOT-01：静态扫描能发现并阻断上述旁路路径（测试只断言“命中规则”，不依赖真实外部服务）。

### 8.9 Tools（Tool Catalog/Selector/Broker：渐进式可见性与单入口执行）

定位：把“工具选择”与“工具执行”拆成可审计、可回归的工程契约。模型只看到候选工具的最小元数据子集（tool_meta_pack），任何副作用工具执行必须经 ToolBroker 单入口并受 Governor/Token 治理。

覆盖需求：FR-TOOL-001~003、FR-SKILL-002、FR-AUDIT-001、NFR-SEC-001。

8.9.1 ToolMeta（仅元数据）最小字段（必须语义等价）
- tool_id（稳定 ID）、name、desc
- args_schema_hash（参数 Schema 哈希；禁止上行完整实现细节）
- side_effect_level：READ|WRITE|EXEC
- auth_level：NONE|APPROVAL|CONFIRM
- domain_tags（用于 Top-K 选择）
- version（工具元数据版本）

8.9.2 tool_meta_pack（Top-K 可见工具子集）
- 语义：每轮仅暴露 Top-K 个 ToolMeta；默认不包含 WRITE；即使包含也不等价于可执行。
- 必须可追溯：tool_visibility_hash 必须与 intent_log（proposal）/llm_call_log 串联。

8.9.3 tool_visibility_hash（可追责口径）
- 计算材料：按稳定排序的（tool_id + args_schema_hash + side_effect_level + version）序列计算摘要。
- 记录位置：intent_log.tool_visibility_hash + llm_call_log.tool_visibility_hash（二者至少其一，且可关联到同一 request_id/intent_id）。

8.9.4 ToolBroker（工具执行单入口）
- 输入契约（概念级）：{intent_id, tool_id, args, approval_token_ref?, confirm_token_ref?}
- 输出契约（概念级）：{status, evidence_ref, tool_result_digest, ts_ms}
- 约束：
  - WRITE/EXEC：必须 Governor=APPROVE 且 token 合法；否则拒绝并写入可统计 reason_code。
  - 任一成功执行必须产出 evidence_ref，且可在审计链中回放到 skill_exec_log（或等价事实日志）。

8.9.5 回归测试编号（必须）
- T-Tool-01：ToolMeta 字段完整性与“仅元数据”约束（不得混入执行实现细节）
- T-Tool-02：Top-K 子集与 WRITE 默认不可见（策略稳定、可统计）
- T-Tool-03：ToolBroker 单入口与 WRITE Token gating（无 token 必拒绝，成功必有 evidence_ref 串联）

### 8.10 MCP（provider→ToolMeta 映射 + 受治理调用）

定位：把 MCP provider 纳入工具元数据体系与审计链；对 MCP 的 READ/WRITE 调用施加同等治理，不允许“外部工具直通执行”。

覆盖需求：FR-MCP-001、FR-TOOL-001~003、FR-AIRLOCK-001、FR-AUDIT-001。

契约点（必须语义等价）：
- provider_id、provider_version → ToolMeta.version
- provider_tool_id → ToolMeta.tool_id（需命名空间隔离，避免与本地工具冲突）
- side_effect_level：READ/WRITE 分级；WRITE 必须走 Token gating
- 审计字段：provider_id/tool_id/request_id/evidence_ref（可回放链路）

回归测试编号（必须）：
- T-MCP-01：provider→ToolMeta 同步稳定性 + WRITE 无 token 必拒绝 + 审计串联完整

### 8.11 Channels（微信）与 Avatar（VTS 外显）治理契约

定位：把“通道 I/O”与“外显驱动”纳入确定性映射与审计链，拒绝模型自由指定隐式参数（收件人/自由动作/任意 expression_id）。

覆盖需求：FR-CHANNEL-001、FR-AVATAR-001、FR-AUDIT-001、NFR-SEC-001。

8.11.1 微信通道（I/O 事件化）
- 入站：inbound event 必须生成稳定 thread_key/message_id/request_id，并写入 channel_io_log（或等价日志）。
- 出站：outbound event 必须绑定 intent_id；发送失败必须以 reason_code + stage 可解释。
- 禁止：LLM 不得直接决定“发给谁/发什么”，只能提出 intent_draft，由 Governor 裁决与票据化执行。

回归测试编号：
- T-Channel-01：入站去重（重复消息不重复触发执行）、出站可追溯（thread_key/request_id/intent_id 串联）

8.11.2 VTS 外显（确定性映射）
- 固定映射：emotion_tag→expression_id（同版本固定），且必须可审计回放。
- talk gate：start/stop_talking 只允许由系统事件触发（例如播放队列状态），不得由模型任意触发。
- 拒绝：未知 emotion_tag 或自由动作参数必须拒绝并留痕。

回归测试编号：
- T-Avatar-01：固定映射一致性 + 非法 tag 拒绝 + 可回放审计字段

8.11.3 VOICE（语音链路：ASR/TTS/打断/兜底模型）
覆盖需求：FR-VOICE-001~005、FR-AUDIT-001、NFR-SEC-001。

契约点（必须语义等价）：
- ASR 上送事件（asr_chunk）：{request_id, ts_ms, text, t_start_ms, t_end_ms, confidence, alternates?}
- 播报请求（speak）：{request_id, text, emotion_tag, voice_profile_id?, priority?}
- 打断事件（barge_in）：{request_id, reason, ts_ms}
- 兜底模型端点输出：必须单个合法 JSON，且 draft_only=true；不得出现 decision/allow/deny/confirm_required 等裁决字段；不得出现任何执行事实宣告。

回归测试编号（新增，必须）：
- T-Voice-01：asr_chunk 字段结构稳定 + 低置信度显式标注 + 可回放
- T-Voice-02：speak→合成→播报事件串联 request_id + 失败 reason_code/stage
- T-Voice-03：Barge-in 生效（播报停止/降级）+ ASR 不中断 + 审计有记录
- T-Voice-04：Edge 兜底 LLM draft-only 输出约束 + 不可用快速失败回退
- T-Voice-05：资源闸门优先级（ASR 优先）+ 峰值互斥行为可观测

## 8.12 VISION（屏幕监控/OCR/摄像头：Gaming 门控 + 事件化 + 证据引用）

覆盖需求：FR-VISION-003~004、FR-MODE-001~002、FR-AUDIT-001~002。
回归口径：（新增）T-VisionGate-01、T-Vision-01、T-Vision-02。

### 8.12.1 屏幕监控与 OCR（按需触发）
定位：屏幕监控与 OCR/识图属于“按需推理能力”，必须受 sys_mode/Gaming 门控，且结果必须结构化、可回放、可引用证据；不得以自然语言替代证据引用。

契约点（必须语义等价）：
- 触发门控：
  - sys_mode=GAMING：禁止触发任何屏幕监控/OCR（含定时轮询与被动触发）；必须拒绝或稳定降级，并返回可统计 reason_code。
  - 非 GAMING：允许按需触发，但必须产出结构化输出，并可追溯 source_ref。
- OCR 输出结构（最小字段集）：
  - {request_id, ts_ms, source_ref, engine_id, text_blocks[]}
  - text_blocks[]：{text, bbox, conf}
  - 约束：bbox 坐标系必须固定；conf 必须显式给出；source_ref 必须可回放到证据材料（Vault artifact/manifest/evidence_ref 之一，取其一但口径必须固定）。
- 失败/降级口径：
  - Engine 不可用/超时：不得伪装为“没有文字/看不到内容”；必须以 reason_code + quality.stage 明确标注降级阶段。

回归测试编号（新增，必须）：
- T-VisionGate-01：Gaming 模式下任意屏幕监控/OCR 请求均被拒绝或降级（reason_code 稳定可统计）；不得出现“我看到了屏幕内容”的无证据宣告。
- T-Vision-01：非 Gaming 下 OCR 输出结构稳定（text_blocks/bbox/conf/source_ref）；source_ref 可回放引用。

### 8.12.2 摄像头识别（事件化输出 + evidence_ref）
定位：摄像头识别必须输出事件化结构，并与审计链串联；低置信度必须显式标注；证据缺失时必须稳定拒绝/降级。

契约点（必须语义等价）：
- camera_event 输出结构（最小字段集）：
  - {request_id, ts_ms, evidence_ref, objects[]}
  - objects[]：{label, conf, bbox?}
  - 约束：evidence_ref 必须能回放到证据材料（Vault artifact/manifest/evidence_ref 之一，取其一但口径必须固定）。
- 低置信度策略：
  - conf 低于阈值时必须显式标注（例如 low_confidence=true 或以 reason_code/notes 枚举表示），不得“强断言”。

回归测试编号（新增，必须）：
- T-Vision-02：摄像头识别事件可回放并指向 evidence_ref；证据缺失时稳定降级/拒绝且 reason_code 可统计。

### 8.12.3 reason_code 与审计串联（VISION 专用）
- VISION 相关拒绝/降级必须具备稳定 reason_code（枚举化），并在 quality.stage 标注失败阶段。
- 所有 VISION 结果必须可串联到 request_id/intent_id（至少其一），并可由审计回放定位到证据引用（source_ref/evidence_ref）。

---

## 9. QA：输出合规模块（Host 必须）
`core/qa.py` 负责对 LLM 输出做“合规检查与重写一次”：
- 伪执行宣告（FALSE_EXECUTION_CLAIM）
- Canon 当事实（CANON_AS_FACT_VIOLATION）
- 非法语言/口吻（LANGUAGE_POLICY_VIOLATION / TONE_POLICY_VIOLATION）
- JSON 结构漂移（LLM_FORMAT_DRIFT）

策略：
- 允许重写 1 次；仍失败 → Host 最小模板输出（并记录 reason_code）

---

## 10. 最小可交付里程碑（MVP→LSP）

### M0（可跑通）
- Host `/v1/chat`：输入→调用 LLM stub/fixtures→QA→输出
- Intent 宽松解析 + 严格校验
- Governor REJECT/APPROVE + reason_code + token_ref
- Skills 框架：至少实现 `narrative_only`（不执行）与 `memory_recall`（只读）
- SQLite audit 最小三表：intent/governor/skill_exec

### M1（安全闭环）
- approval_token 验证
- 重放保护：id/seq/ts/nonce
- reason_code 枚举与测试（T-16/T-17）
- 静态扫描禁止旁路

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
- T-Canon-01~T-Canon-02 Style Canon 版本化与 Canon-as-fact 拦截
- T-WCanon-01~T-WCanon-03 World Canon 索引同步/引用注入/非执行事实口径回归
- T-Flow-01~T-Flow-02 配置资产（Flow）：指针合法性 + Trace 索引一致性

---

## 12. Codex 执行说明

你在每次让 Codex 改代码前，必须提供：
- 目标里程碑（M0/M1/M2/M3）
- 要改的文件列表（全文件替换优先）
- 需要通过的测试编号（例如 T-14、T-18、T-34）
- 不变量（例如 I1/I8/I9）不可破坏
- 输出格式：必须给出变更摘要 + 变更文件列表 + 下一步建议

（更完整的 Codex 行为规范在 `docs/40_agents/AGENTS.md`。）

---

## 13. 环境与配置（dev/test/prod）

定位：本章定义“环境枚举、配置来源优先级、敏感信息边界、外部依赖隔离口径”。不包含任何命令行或执行步骤。

覆盖需求：NFR-SEC-001、NFR-REL-001、NFR-MAINT-001（以及 ModelPlane/AIRLOCK 相关落点）
回归口径：T-Perf-01、T-Gaming-01、T-39/T-40、T-Flow-01/02（配置一致性）

### 13.1 环境枚举（语义固定）
- Natsume_ENV=dev：本地开发（允许 stub/fixtures；更详细日志；允许关闭部分外部依赖但必须可降级）
- Natsume_ENV=test：自动化测试（强制隔离外部依赖；使用临时库/临时目录；不得访问真实密钥）
- Natsume_ENV=prod：真实运行（最小暴露；严格门禁默认；危险能力默认关闭或需显式授权）

### 13.2 配置来源优先级（从高到低）
1) 环境变量（运行时注入）
2) 本地覆盖配置（仅 dev/test，且不得入库）
3) 仓库内默认配置（安全默认值；必须能跑最小闭环）

硬约束：
- 任何 secret（API key、token、账号凭据）不得出现在仓库文本文件中；只能以键名存在（例如 OPENAI_API_KEY）。
- 任何出网 endpoint 只能在 secret/环境层提供；仓库内仅允许 provider 名称与模型标识（见 ModelPlane 配置资产）。

### 13.3 配置资产入口（与文档/Trace 的关系）
- Flow 配置资产：flows/exports + flows/active.pointer；其一致性由 docs/60_trace/flows/index.md 指向并可回归（T-Flow-01/02）。
- ModelPlane 配置资产：configs/model_plane/node_registry.yaml + model_profiles.yaml；其变更必须 bump model_profile_id，并写入 llm_call_log（或等价审计字段）。
- LLM 输出契约资产：prompts/llm_contract/*（contract 示例与拒绝范式）；不得当需求来源，只当契约/回归输入源。

### 13.4 外部依赖隔离要求（test 环境强制）
- test 环境不得连接真实 Neo4j、真实向量库、真实云端模型服务；必须使用 stub/fixtures 或替身实现。
- 出网相关模块必须支持“airlock stub”：仍产生 airlock_log 结构化记录，但不会真实发送。
- ClawdBot/UI 类能力在 test 环境必须默认禁用或模拟（只验证日志/契约与红线熔断口径）。

### 13.5 Gaming 模式资源策略（显存被游戏占用）
- Host 不得依赖本地 GPU 模型；只允许规则引擎/模板/CPU tiny model（如存在）。
- 云端调用允许继续，但必须受超时退化策略约束，避免沉默（T-Perf-01、T-Gaming-01）。
- 渲染优先走云端 renderer；云不可用时退化为 Host 最小日文模板（不得加料）。

---

## 14. 实施检查清单（Codex 执行用）

### 14.1 总览（体系结构与数据流）
必须清晰表达并在实现中保持一致的事实：
- 单向链：输入 → LLM（narrative + intent_draft 草案）→ Host（Governor 裁决）→ Skills/Actuators（执行）→ Audit（事实）
- “事实来源”：仅审计链（intent/governor/skill_exec）+ Hard State/SSOT；LLM 叙事不构成事实

### 14.2 模块边界（可静态扫描验证）
边界定义（必须满足）：
- Host：Hard State / Governor / RAG 主权 / Audit / Bond Dynamics / Evolution
- LLM：只负责叙事生成 + Intent 草案（禁止执行、禁止裁决、禁止宣称事实）
- Edge：感知/执行端（RPC + 签名 + 重放保护），不得有裁决逻辑

### 14.3 协议与 Schema（强类型 + 可回放）

目标：把“语言→行为”的唯一入口限定为强类型对象，并能稳定拒绝、稳定审计、稳定回放。

必须存在且保持一致的契约（最小集）：
1）Intent（见第 3 章）
- lenient parse：只修语法不补语义；失败 INTENT_PARSE_FAILED
- strict validate：字段/类型/枚举/args/重放/时效；失败映射 INTENT_SCHEMA_INVALID / INTENT_ARGS_INVALID / REPLAY_DETECTED / SEQ_INVALID / EXPIRED_INTENT
- allowlist：type/action/target

2）GovernorOutput（见第 4 章）
- decision/reason_code/rule_trace/tokens/snapshot_refs 结构固定
- REJECT 必带 reason_code + rule_trace
- APPROVE 必带 approval_token_ref；缺失必须触发执行入口拒绝（SKILL_TOKEN_MISSING）

3）Token/Manifest（见第 5 章）
- approval_token：绑定 intent_id + skill_name + session_id + seq + TTL
- confirm_token：高风险二次提交证明（不得用语言确认替代）
- undo_token + manifest：可逆默认（digest 必须可核验）

验收点（口径）：
- T-18~T-22（Intent）
- T-14~T-17（GovernorOutput）
- T-34~T-38（Token/Undo）

### 14.4 关键规则与算法（可版本化、可回归）

目标：把裁决与授权的关键逻辑做成“版本化规则”，而不是散落在业务代码里。

必须实现且可版本化的规则族：
1）Governor Gates（R1–R7）
- trust / energy / sys_mode / rate_limit / scope_policy / safety / airlock
- 每次裁决必须产出 rule_trace（包含命中门、阈值摘要、快照摘要）

2）工具与执行门禁
- WRITE 类副作用能力必须经 Token（必要时 confirm_token）
- 工具可见性必须渐进式加载（Top-K 元数据子集）并记录 tool_visibility_hash

3）幂等与重放
- intent_id 幂等：重复 intent 不得产生第二次副作用
- seq/nonce/ts_ms：重放窗、乱序、过期均可解释拒绝

验收点（口径）：
- T-Gov-01（rule_trace 可定位）
- T-SSOT-01（旁路禁止/单写者）
- T-Tool-01~03（若 Tool 平面已纳入 DevTasks）
- T-18~T-22（重放/顺序/过期）

### 14.5 失败与降级（状态机 + 一次重写）

目标：链路任何阶段失败，系统也必须输出“稳定结构 + 明确 reason_code”，且绝不编造事实。

必须有的机制：
1）分阶段状态机（S0..S8，或语义等价）
- 每阶段定义：成功条件/失败条件/降级策略/审计记录位置/对外 reason_code

2）QA 模块（见第 9 章）
- 拦截：伪执行宣告、Canon-as-fact、口吻/语言违规、JSON 漂移
- 允许重写 1 次；仍失败必须降级到 Host 最小模板输出（不得承诺执行）

3）依赖不可用（DB/Neo4j/Vector/Cloud）
- 不崩溃：仍输出可解析结构
- 可观测：error_log/quality.stage 可定位失败点
- 更保守：执行类能力默认拒绝或降级为草案

验收点（口径）：
- T-67~T-71（降级与 QA）
- T-Perf-01（超时退化与在场回应）
- T-39/T-40（airlock 失败路径）

### 14.6 验收测试落地方式（T-xx → tests/contract + tests/e2e）

目标：把 DevSpec 的“契约与断言”落成两类回归：契约测试（快速）+ 最小闭环（可回放）。

1）contract tests（快速、无外部依赖优先）
必须覆盖：
- Schema/Enum：Intent、GovernorOutput、Token、Manifest、reason_code 枚举稳定
- 指针一致性：Flow active.pointer 与 Trace 索引一致性（T-Flow-01/02）
- 单写者与旁路禁止：静态扫描断言（T-SSOT-01）
- LLM 输出契约 lint：Strict JSON（T-LLM-01）

2）e2e tests（最小闭环）
最小链路口径：
- Host 输入 → LLM stub/fixtures → QA →（可选）Governor → Skill stub → Audit
必须断言：
- 三联画可串联：intent_log → governor_log →（可选）skill_exec_log
- REJECT 必带 reason_code
- APPROVE 必带 approval_token_ref；无 token 不得执行

建议最小覆盖集（随里程碑扩展）：
- T-14/T-15
- T-18~T-22
- T-34~T-38
- T-67~T-71
- T-Flow-01~02

### 14.7 新增验收测试编号（VOICE/VISION/RETRIEVAL/EDGE）
以下测试编号用于覆盖本次对话新增的服务项清单与语音/视觉/检索链路契约（不写命令，仅定义断言点）：

- T-80（VOICE-ASR）：ASR 上送事件结构稳定（timestamps/confidence/alternates）；低置信度显式标注；可回放。
- T-81（VOICE-TTS）：Host 下发文本→Edge 合成播报；失败返回 reason_code/stage；播报事件可与 request_id 串联。
- T-82（VOICE-BARGEIN）：播报中用户开口触发打断；播放停止/降级；ASR 继续上送；审计记录打断事件。
- T-83（EDGE-FALLBACK-LLM）：兜底 LLM 输出为 draft-only JSON；不得出现执行事实宣告；不可用时快速失败并触发 Host 兜底口径。
- T-84（VISION-SCREEN-GATE）：Gaming 模式禁止屏幕监控/OCR/Clawd UI；非 Gaming 模式按需触发并事件化审计。
- T-85（RETRIEVAL-PIPE）：embedding+rerrank+recall/fusion 输出 TopK evidence pack 字段稳定；缺任一服务时降级口径与 reason_code 稳定。
- T-86（BOND-NEO4J）：Neo4j 单写者边界生效；bond_summary 派生不含执行事实；不可用时降级口径稳定。
- T-87（OBS-RESOURCE）：Edge 语音链与 GPU 显存指标可观测；异常峰值可定位 stage 与组件。

### 14.8 新增验收测试编号（M4 体验包：SCHEDULER/DESKTOP/DREAM/DIARY/PROACTIVE/FOCUS）

以下测试编号用于覆盖 M4 体验包新增功能的契约与行为断言：

- T-Sched-01（SCHEDULER 注册与触发）：调度器注册/注销/触发可回放；system_initiated_intent 带 proposer_node_id=scheduler；触发频率符合配置。
- T-Sched-02（SCHEDULER 模式门控）：Gaming 模式拦截低优先级任务（SYSMODE_FORBIDDEN）；Sleep 模式仅允许白名单任务；Focus 子模式暂停非紧急任务。
- T-Sched-03（SCHEDULER 健康与降级）：连续失败自动暂停（SCHED_TASK_SUSPENDED）；健康快照可读取。
- T-Desktop-01（桌面留言创建与撤销）：文件创建成功 + manifest 完整（before/after/path/hash）；undo 恢复原状态；路径穿越拒绝（SCOPE_VIOLATION）。
- T-Desktop-02（桌面留言 QA）：内容含伪执行句时 QA 拦截并重写；写入审计链完整。
- T-Dream-01（梦境生成与标签）：Sleep/空闲触发成功；写入 Episodic 带 dream 标签 + NARRATIVE_ONLY；Normal 非空闲拒绝。
- T-Dream-02（梦境非事实约束）：召回梦境时 evidence_level=NARRATIVE_ONLY 存在；QA 不升级为事实。
- T-Diary-01（日记生成闭环）：定时触发成功；文件创建 + Episodic 写入；无当日对话时不伪造。
- T-Diary-02（日记 manifest 与撤销）：日记文件 manifest 完整；undo 可回滚。
- T-Proactive-01（主动对话触发与门控）：空闲触发成功 + 审计链完整；Gaming 拒绝；频率超限拒绝（RATE_LIMIT_EXCEEDED）。
- T-Proactive-02（主动对话内容约束）：内容不含执行 intent；推送失败静默降级不重试。
- T-Focus-01（Focus Mode 切换）：进入/退出可审计；主动对话被拦截；用户主动提问正常响应；退出后调度器恢复。

---

## 15. M4 体验包工程契约（SCHEDULER/DESKTOP/DREAM/DIARY/PROACTIVE/FOCUS）

定位：本章定义 M4 体验包的工程实现契约——调度器基础组件 + 5 个体验功能的 Skill 定义、触发策略、content_pack 注入、QA 约束与审计口径。

覆盖需求：FR-SCHED-001~002、FR-DESKTOP-001~002、FR-DREAM-001~002、FR-DIARY-001~002、FR-PROACTIVE-001~002、FR-FOCUS-001。
回归口径：T-Sched-01~03、T-Desktop-01~02、T-Dream-01~02、T-Diary-01~02、T-Proactive-01~02、T-Focus-01。

### 15.1 Scheduler（定时调度器）

落点：`host_brain/scheduler/` 目录（新增）。

15.1.1 核心组件
- `scheduler.py`：调度器主体（基于 APScheduler 或等价轻量库），支持 cron/interval/once 三种触发模式。
- `task_registry.py`：调度任务注册表（task_id/trigger_config/intent_template/priority/mode_allowlist/enabled/max_consecutive_failures）。
- `health.py`：任务健康快照（task_id/last_run_ts/next_run_ts/status/error_count/suspended_reason）。

15.1.2 Intent 生成约定
- 每次触发生成 `system_initiated_intent`，字段与用户 Intent 完全一致，但 `proposer_node_id=scheduler`。
- Intent 进入与用户请求相同的编排入口（`/v1/chat` 内部路径或等价编排调度函数），走 Governor→Token→Skill→Audit。

15.1.3 sys_mode 门控矩阵

| 任务优先级 | Normal | Sleep | Gaming | Focus（Normal子模式） |
|-----------|--------|-------|--------|---------------------|
| HIGH（如日记） | ✅ | ✅ | ❌ | ✅ |
| MEDIUM（如梦境） | ✅ | ✅ | ❌ | ❌ |
| LOW（如主动对话） | ✅ | 仅问候 | ❌ | ❌ |

15.1.4 审计字段
- `scheduler_event_log`（新增审计表）：task_id/trigger_ts/intent_id/status/reason_code/mode_at_trigger/error_detail。

### 15.2 Desktop Note Skill（桌面留言）

落点：`host_brain/skills/impl/desktop_note.py`（新增）。

15.2.1 SkillMeta
```
name: desktop_note
clearance_level: L1
risk_level: 2
reversible: true
scopes: [filesystem]
requires_visual_audit: false
requires_confirm_token: false
data_classification: INTERNAL
rate_limit: 10/hour
energy_cost: low
```

15.2.2 执行契约
- 输入 args：{action: create|update|delete, filename: str, content: str, base_dir_key: str}
- base_dir_key 映射到配置中的安全基目录（如 `NATSUME_DESKTOP_DIR`）；路径穿越检查（`..`/symlink 拒绝）。
- create/update：写入文件 → 产出 manifest（before_hash/after_hash/path）+ undo_token。
- delete：move-to-quarantine（Vault quarantine/），不真删；真删需 confirm_token。
- 内容经 QA 校验（不得含伪执行句）。

15.2.3 reason_code 映射
- 路径穿越：SCOPE_VIOLATION
- 基目录不可写：SKILL_EXEC_FAILED（子类 FS_WRITE_FAILED）
- QA 拦截：FALSE_EXECUTION_CLAIM（重写后重试 1 次）

### 15.3 Dream Skill（梦境生成）

落点：`host_brain/skills/impl/dream_generator.py`（新增）。

15.3.1 SkillMeta
```
name: dream_generator
clearance_level: L0
risk_level: 1
reversible: true
scopes: [memory]
requires_visual_audit: false
requires_confirm_token: false
data_classification: INTERNAL
rate_limit: 2/day
energy_cost: medium
```

15.3.2 执行契约
- 触发条件由 Scheduler 判定（sys_mode=SLEEP 或 idle_duration ≥ 阈值）。
- 读取当日 Episodic Memory（按 ts_ms 过滤当日条目，TopK=20）。
- 打包 context_pack（含 bond_summary + 当日 episodic 摘要）→ LLM 生成梦境叙事。
- 写入 Episodic：category=dream, evidence_level=NARRATIVE_ONLY, evidence_refs=[intent_id]。
- manifest：memory_before_count/memory_after_count/dream_id/hash。

15.3.3 内容标签与口径
- 写入时必须标注 `evidence_level=NARRATIVE_ONLY`。
- 召回时 context_pack 的 retrieval.episodic 槽位需保留此标注，QA 据此判断"不可升级为事实"。

### 15.4 Diary Skill（日记生成）

落点：`host_brain/skills/impl/diary_writer.py`（新增）。

15.4.1 SkillMeta
```
name: diary_writer
clearance_level: L0
risk_level: 1
reversible: true
scopes: [memory, filesystem]
requires_visual_audit: false
requires_confirm_token: false
data_classification: INTERNAL
rate_limit: 1/day
energy_cost: medium
```

15.4.2 执行契约
- 每日定时触发（如 23:00），由 Scheduler 发起。
- 汇总当日对话：读取 intent_log + governor_log（当日 ts_ms 范围）+ Episodic 当日条目。
- 检查是否有实质对话内容（非空判定）：无对话 → 跳过并记录 SCHED_NO_CONTENT（不伪造日记）。
- 有对话 → 打包 context_pack → LLM 生成日记叙事 → 双写：
  - 本地文件：`{base_dir}/diary/{YYYY-MM-DD}.md`，产出 file manifest。
  - Episodic Memory：category=diary, evidence_refs=[intent_id, file_path]。
- QA 校验：日记内容不得含伪执行句。

15.4.3 Evolution 提案（可选扩展）
- 日记 LLM 输出可选包含 `drift_suggestion` 字段。
- 若存在：生成 canon_proposal → 写入 canon_proposal_log → 待 Host 白名单/Evolution 审核。
- 不存在：跳过（默认路径）。

### 15.5 Proactive Conversation Skill（主动对话）

落点：`host_brain/skills/impl/proactive_talk.py`（新增）。

15.5.1 SkillMeta
```
name: proactive_talk
clearance_level: L0
risk_level: 1
reversible: false
scopes: [dialogue]
requires_visual_audit: false
requires_confirm_token: false
data_classification: NONE
rate_limit: 3/hour
energy_cost: low
```

15.5.2 触发策略（Scheduler 任务配置）
- 条件组合（AND 逻辑）：
  - idle_duration ≥ 阈值（默认 30 分钟，可配置）
  - bond.stage ≥ threshold（默认 familiar 以上）
  - sys_mode ∈ [NORMAL]（Gaming/Sleep/Focus 均禁止或仅极低频问候）
  - 当前小时 ∈ active_hours（默认 8:00~23:00）
- 频率限制：每小时最多 3 次（ Rate Limit R4 Gate 生效）。

15.5.3 执行契约
- 打包 context_pack（含 bond_summary + 最近 episodic + 当前时间段信息）→ LLM 生成主动对话草案。
- QA 校验：内容仅限问候/关心/回忆引用/提醒口径；含执行 intent → 拦截重写。
- 推送路径：
  - Edge 可达 → RPC speak（语音）或 text push；
  - 微信通道可用 → 通过 wechat outbound 推送；
  - 均不可达 → 静默降级（记录 DEPENDENCY_UNAVAILABLE），不重试推送（防骚扰）。
- 审计：proactive_event_log（新增或复用 skill_exec_log）记录触发条件快照、推送结果、reason_code。

### 15.6 Focus Mode（专注模式状态扩展）

落点：`host_brain/core/metabolism.py`（扩展现有 sys_mode 状态机）。

15.6.1 状态定义
- Focus 为 Normal 的子模式：{sys_mode: NORMAL, sub_mode: FOCUS}。
- 进入条件：用户指令 / 定时策略 / 日历事件（均需 evidence）。
- 退出条件：用户指令 / 定时策略到期 / 超时自动退出。

15.6.2 能力矩阵影响
- 主动对话：❌ 暂停（SYSMODE_FORBIDDEN）
- 梦境/日记等非紧急调度任务：❌ 暂停
- 桌面留言（用户请求触发）：✅ 允许
- 用户主动提问：✅ 正常响应
- 通知推送：❌ 静默

15.6.3 审计
- 进入/退出写 hard_state_log（原因、证据摘要、持续时长）。
- 被拦截的调度任务写 scheduler_event_log（reason_code=SYSMODE_FORBIDDEN_FOCUS）。

---

## 16. 性能预算与时延可观测（NFR-PERF-001）

定位：本章定义"响应时延预算"的工程契约——采集方式、阈值口径、超时退化策略与可观测报告结构，使 NFR-PERF-001 从 PRD 的定性描述变为可验证的工程基线。

覆盖需求：NFR-PERF-001。
回归口径：T-Perf-01（时延预算基准与超时退化）。

### 16.1 时延预算定义（基准机器口径）

基准机器：Host 侧 i7-12700 / 32GB RAM / SSD；Edge 侧 RTX 3060 12GB VRAM（或等价）。

| 链路阶段 | 指标 | p50 目标 | p95 目标 | 超时硬限 |
|----------|------|---------|---------|---------|
| 普通对话（无执行） | 用户输入→Host 输出 narrative | ≤ 2000ms | ≤ 5000ms | 10000ms |
| 执行链路（含 Skill） | 用户输入→skill_exec_log 写入 | ≤ 3000ms | ≤ 8000ms | 15000ms |
| 检索链路（Embedding+Rerank+Fusion） | query→evidence_pack 输出 | ≤ 500ms | ≤ 1500ms | 3000ms |
| LLM 外部调用（单次） | request→response | ≤ 1500ms | ≤ 4000ms | 8000ms |
| Ledger 写入（单条） | write→commit | ≤ 5ms | ≤ 20ms | 100ms |
| Edge ASR（单段转写） | audio_chunk→asr_chunk | ≤ 300ms | ≤ 800ms | 2000ms |
| Edge TTS（合成+播放启动） | text→audio_start | ≤ 500ms | ≤ 1500ms | 3000ms |

约束：
- 阈值为指导性基线（非硬门禁），超出 p95 时必须在 quality.stage 中标注 PERF_DEGRADED 并记录 reason_code=PERF_TIMEOUT 或更精确子类。
- 超时硬限到达时必须触发超时退化策略（见 16.2），不得无限阻塞。

### 16.2 超时退化策略

- LLM 调用超时：进入 Host 最小模板输出（不伪执行），quality.stage 标注 LLM_TIMEOUT，reason_code=PERF_LLM_TIMEOUT。
- 检索链路超时：返回空 evidence_pack（字段稳定）+ quality.stage=RETRIEVAL_TIMEOUT，reason_code=PERF_RETRIEVAL_TIMEOUT。
- Skill 执行超时：Governor 批准后执行超时，记录 skill_exec_log（result_status=TIMEOUT）+ reason_code=PERF_SKILL_TIMEOUT，返回降级输出。
- Edge 超时（ASR/TTS）：Host 侧标注 DEPENDENCY_TIMEOUT，不阻塞主输出。

### 16.3 时延采集与报告结构

采集点（必须）：
- Host 主链路：request_start_ms / llm_call_start_ms / llm_call_end_ms / governor_start_ms / governor_end_ms / skill_start_ms / skill_end_ms / response_end_ms。
- 检索链路：embed_start_ms / embed_end_ms / rerank_start_ms / rerank_end_ms / fusion_end_ms。
- Edge 链路：asr_start_ms / asr_end_ms / tts_start_ms / tts_audio_start_ms。

采集写入位置：
- 每次请求在审计链扩展字段中记录 `latency_breakdown`（JSON 对象，含上述时间戳对）。
- 定期聚合报告（由 bench 脚本或观测面板按时间窗口输出 p50/p95/p99 统计）。

报告结构（最小字段）：
```
{
  "report_ts": "ISO8601",
  "window_ms": 3600000,
  "sample_count": 100,
  "stages": {
    "e2e_chat": {"p50_ms": ..., "p95_ms": ..., "p99_ms": ..., "timeout_count": ...},
    "llm_call": {"p50_ms": ..., "p95_ms": ..., "p99_ms": ..., "timeout_count": ...},
    "retrieval": {"p50_ms": ..., "p95_ms": ..., "p99_ms": ..., "timeout_count": ...},
    "ledger_write": {"p50_ms": ..., "p95_ms": ..., "p99_ms": ..., "timeout_count": ...}
  }
}
```

### 16.4 新增验收测试编号

- T-Perf-01（性能预算基准与超时退化）：
  - 在基准机器上运行 bench 脚本，验证各阶段 p50/p95 是否在目标范围内；
  - 模拟 LLM/检索/Skill 超时场景，验证退化策略生效（输出结构稳定 + quality.stage 标注超时阶段 + reason_code 可统计）；
  - Ledger 写入性能基准（复用 ledger_bench.py）满足 p95 ≤ 20ms。

### 16.5 新增错误码

- PERF_TIMEOUT：通用时延超限
- PERF_LLM_TIMEOUT：LLM 调用超时
- PERF_RETRIEVAL_TIMEOUT：检索链路超时
- PERF_SKILL_TIMEOUT：Skill 执行超时

---

## 17. M7 可观测 + MCP + 语音深化（工程契约）

> 对应 PRD §4.16（FR-OBS）、§4.18（FR-MCP）、§4.19（FR-VOICE）。
> 当前状态：PRD_READY → 本章节完成后进入 SPEC_READY。

---

### 17.1 可观测与运维（FR-OBS-001/002）

#### 17.1.1 关键指标面板（FR-OBS-001）

**指标采集点（必须）**：
- `governor_decision_total`：按 decision（APPROVE/REJECT/CONFIRM_REQUIRED）+ reason_code 分组计数。
- `pseudo_exec_violation_total`：QA 拦截的伪执行宣告次数（reason_code=PSEUDO_EXEC_VIOLATION）。
- `retrieval_evidence_count`：每次召回融合的 evidence 候选数（p50/p95）。
- `hard_snapshot_age_ms`：Hard Snapshot 距上次更新的时间差（可观测 staleness）。
- `chain_summary_count`：最近 N 条链路摘要（request_id/intent_id/decision/stage/ts_ms）。

**面板接口**：
```
GET /obs/metrics
→ {
    "governor_decisions": {"APPROVE": int, "REJECT": int, "CONFIRM_REQUIRED": int},
    "top_reject_codes": [{"reason_code": str, "count": int}],
    "pseudo_exec_violations": int,
    "retrieval_p50": float,
    "hard_snapshot_age_ms": int,
    "recent_chains": [{"request_id": str, "decision": str, "stage": str, "ts_ms": int}]
  }
```

**日志字段对齐**：所有指标必须可通过 `request_id` / `intent_id` 串联到 SQLite Ledger。

**验收测试编号**：T-OBS-01（指标面板结构稳定 + request_id 串联可回放）

#### 17.1.2 开发/生产模式差异（FR-OBS-002）

**模式切换**：通过 `OBS_MODE=dev|prod` 环境变量控制（或 config 字段）。

**行为差异**：
- `dev` 模式：`/obs/metrics` 额外返回 `debug_trace`（含 LLM 原始 intent_draft、Governor 规则命中路径、Skill 执行参数）。
- `prod` 模式：`debug_trace` 字段不返回（或返回 null）；仅保留 `user_facing_hint`。

**错误码**：
- `OBS_MODE_INVALID`：obs_mode 值不合法。

**验收测试编号**：T-OBS-02（切换模式后 debug_trace 字段按预期出现/消失；不影响裁决与审计事实）

---

### 17.2 MCP Provider 治理（FR-MCP-001）

#### 17.2.1 ToolMeta 同步与分级

**ToolMeta Schema**：
```json
{
  "provider_id": "string",
  "tool_id": "string",
  "name": "string",
  "description": "string",
  "access_level": "READ | WRITE",
  "risk_level": 1,
  "reversible": true,
  "requires_token": false,
  "schema_version": "string",
  "synced_at_ms": 0
}
```

**同步规则**：
- MCP provider 暴露的工具必须先同步为 ToolMeta（仅元数据），不得直接调用。
- ToolMeta 写入 `mcp_tool_registry`（SQLite 表），变更写 `mcp_sync_log`（可回放）。

#### 17.2.2 ToolBroker 单入口

**调用链**：`intent_draft.mcp_call → ToolBroker → Governor gate → Token（WRITE 必须）→ MCP SDK → Audit`

**审计字段**（写入 `mcp_exec_log`）：
```json
{
  "request_id": "string",
  "intent_id": "string",
  "provider_id": "string",
  "tool_id": "string",
  "access_level": "READ | WRITE",
  "decision": "APPROVE | REJECT",
  "reason_code": "string | null",
  "evidence_ref": "string",
  "ts_ms": 0
}
```

**门禁规则**：
- `access_level=WRITE` 且无 approval_token → `reason_code=TOKEN_REQUIRED`，拒绝。
- `access_level=WRITE` 且 token 未 APPROVE → `reason_code=TOKEN_NOT_APPROVED`，拒绝。
- AIRLOCK 出站最小化：MCP 调用参数必须经 AIRLOCK 脱敏（敏感字段不出站）。

**错误码**：
- `MCP_TOOL_NOT_REGISTERED`：工具未在 ToolMeta 注册。
- `MCP_WRITE_TOKEN_REQUIRED`：WRITE 操作缺少 token。
- `MCP_PROVIDER_UNAVAILABLE`：provider 不可达，降级。

**验收测试编号**：
- T-MCP-01（WRITE 无 token → TOKEN_REQUIRED 拒绝 + 审计可回放）
- T-MCP-02（READ 无 token → 正常执行 + 审计可回放）
- T-MCP-03（provider 不可达 → MCP_PROVIDER_UNAVAILABLE 降级 + 结构稳定）

---

### 17.3 语音链路深化（FR-VOICE-001~005）

#### 17.3.1 ASR 事件化上送（FR-VOICE-001）

**事件 Schema**（Edge→Host RPC）：
```json
{
  "event_type": "asr_transcript",
  "request_id": "string",
  "session_id": "string",
  "text": "string",
  "confidence": 0.95,
  "is_final": true,
  "ts_start_ms": 0,
  "ts_end_ms": 0,
  "alternates": []
}
```

**约束**：
- `confidence < 0.6` 时必须显式标注 `low_confidence=true`，Host 可选择降级处理。
- 事件写入 `voice_event_log`（含 request_id 串联）。

**错误码**：`ASR_LOW_CONFIDENCE`、`ASR_UNAVAILABLE`

**验收测试编号**：T-VOICE-01（ASR 事件结构稳定 + 低置信度标注 + 审计可回放）

#### 17.3.2 TTS 合成与播报队列（FR-VOICE-002）

**下发 Schema**（Host→Edge RPC）：
```json
{
  "request_id": "string",
  "intent_id": "string",
  "text": "string",
  "emotion_tag": "NEUTRAL | HAPPY | SAD | ANGRY | SURPRISED | THINKING",
  "priority": 1,
  "interruptible": true
}
```

**播报队列规则**：
- 队列按 priority 排序；同优先级 FIFO。
- `interruptible=true` 的任务可被 Barge-in 打断。
- 播报完成/打断后写 `voice_event_log`（含 reason_code 若失败）。

**错误码**：`TTS_MODEL_UNAVAILABLE`、`TTS_QUEUE_FULL`、`TTS_INTERRUPTED`

**验收测试编号**：T-VOICE-02（TTS 下发结构稳定 + 队列优先级 + 失败可解释）

#### 17.3.3 强交互可打断 Barge-in（FR-VOICE-003）

**触发条件**：ASR 检测到用户开口（`is_speaking=true`）时，Edge 立即发送 `barge_in` 事件。

**Host 处理**：
- 收到 `barge_in` → 向 Edge 发送 `tts_interrupt` 指令 → 写 `voice_event_log`（event_type=barge_in）。
- 打断不构成任何执行事实或写入事实（reason_code=BARGE_IN_INTERRUPTED）。

**验收测试编号**：T-VOICE-03（打断立即生效 + 审计记录 + ASR 不被阻塞）

#### 17.3.4 Edge 兜底小模型（FR-VOICE-004）

**触发条件**：Host 主链路不可用（Cloud LLM 超时/不可达）且 Edge 兜底模型已加载。

**约束**：
- 兜底输出必须标记 `draft_only=true`，不得包含裁决或执行事实宣告。
- 默认卸载（`ollama_loaded=false`），按需加载（触发后 ≤30s 内可用）。
- 不可用时 `reason_code=FALLBACK_MODEL_UNAVAILABLE`，快速失败回退到 Host 兜底口径。

**错误码**：`FALLBACK_MODEL_UNAVAILABLE`、`FALLBACK_OUTPUT_DRAFT_ONLY`

**验收测试编号**：T-VOICE-04（兜底输出标记 draft_only + 不可用时快速失败 + 结构稳定）

#### 17.3.5 Edge 资源闸门（FR-VOICE-005）

**优先级矩阵**：

| 优先级 | 任务 | 抢占规则 |
|--------|------|---------|
| P1（最高）| ASR 采集 | 不可被抢占 |
| P2 | 音频播放 | 可被 P1 打断 |
| P3 | TTS 合成 | 可被 P1/P2 打断 |
| P4（最低）| 兜底 LLM 推理 | 可被任意高优先级抢占 |

**资源闸门 Schema**（健康快照）：
```json
{
  "asr_active": true,
  "playback_active": false,
  "tts_synthesizing": false,
  "fallback_llm_loaded": false,
  "gpu_utilization_pct": 45,
  "current_priority": "P1"
}
```

**错误码**：`RESOURCE_GATE_PREEMPTED`（低优先级任务被抢占）

**验收测试编号**：T-VOICE-05（压力下 ASR 优先保证 + 低优先级降级可观测）

---

### 17.4 M7 新增错误码汇总

- `OBS_MODE_INVALID`：obs_mode 值不合法
- `MCP_TOOL_NOT_REGISTERED`：MCP 工具未注册
- `MCP_WRITE_TOKEN_REQUIRED`：MCP WRITE 缺少 token
- `MCP_PROVIDER_UNAVAILABLE`：MCP provider 不可达
- `ASR_LOW_CONFIDENCE`：ASR 置信度低于阈值
- `ASR_UNAVAILABLE`：ASR 服务不可用
- `TTS_QUEUE_FULL`：TTS 播报队列已满
- `TTS_INTERRUPTED`：TTS 被 Barge-in 打断
- `FALLBACK_MODEL_UNAVAILABLE`：Edge 兜底模型不可用
- `FALLBACK_OUTPUT_DRAFT_ONLY`：兜底输出仅为草案
- `RESOURCE_GATE_PREEMPTED`：资源闸门抢占

---

本文件不再维护"PRD→DevSpec→DevTasks→Tests"的覆盖矩阵，以避免重复维护导致的状态过期与冲突。
覆盖关系的唯一真相源是 Trace：docs/60_trace/requirements_trace.md。

本文件（DevSpec）只承担两件事：
1) 把 FR 需求落成可审阅的工程契约（接口/Schema/错误码/日志点/测试点编号 T-xx）。
2) 给出"如何验证"的断言口径（T-xx 的输入/输出/拒绝码/审计字段）。

状态使用建议（Trace 侧枚举为准）：
- PRD_READY / SPEC_READY / TASK_READY / TEST_READY / DONE：用于表达链路成熟度。
- SPEC_GAP / TASK_GAP / TEST_GAP：用于明确"缺口在哪里"，便于下一轮补齐。

---

## §18 M8 运行时集成闭环

> 对应 PRD M8；覆盖 NFR-PERF-001、NFR-REL-001、FR-CORE-004。
> 目标：让 M1~M7 所有已实现模块真正能被一键启动、端到端联通，并建立可回归的性能基线与故障注入矩阵。

### 18.1 运行时脚本集成（TASK-0107）

#### 18.1.1 start_host_side.ps1 集成 OBS 路由与 MCP Broker（NFR-REL-001）

**当前缺口**：`start_host_side.ps1` 启动 `host_brain` 进程后，`/obs/metrics` 路由与 `MCPToolBroker` 已在代码中实现，但启动脚本的健康检查仅探测 `/health`，未验证 OBS 与 MCP 子系统是否正常初始化。

**工程契约**：
- `start_host_side.ps1` 在 `/health` 通过后，额外探测 `/obs/metrics`（期望 HTTP 200）；
- 若 `/obs/metrics` 返回非 200，启动脚本输出 `obs_status=degraded`（不阻断启动，但写入 `startup_warnings`）；
- 启动结果 JSON 新增字段：`obs_status`（ok/degraded）、`mcp_registry_status`（ok/degraded）；
- `mcp_registry_status` 通过调用 `MCPToolRegistry.health_check()` 判断（SQLite 可写即 ok）。

**输出结构扩展**：
```json
{
  "side": "host",
  "status": "started",
  "pid": 12345,
  "obs_status": "ok",
  "mcp_registry_status": "ok",
  "startup_warnings": []
}
```

**错误码**：`STARTUP_OBS_DEGRADED`、`STARTUP_MCP_DEGRADED`

**验收测试编号**：T-RT-01（启动脚本集成：OBS/MCP 子系统健康状态可观测）

---

### 18.2 端到端 Smoke Test（TASK-0108）

#### 18.2.1 冷启动端到端最小闭环（FR-CORE-004）

**目标**：在无外部依赖（无 API Key、无 Neo4j、无 Chroma）的情况下，验证系统能完成"输入→降级叙事→审计记录→稳定输出"的最小闭环。

**测试场景**（T-E2E-01）：
1. 启动 `host_brain`（仅 SQLite，无外部服务）；
2. 发送 `POST /chat`（普通对话，无执行 intent）；
3. 断言：
   - 响应为合法 JSON，`quality.stage` 包含降级标记；
   - `reason_code` 不为空（若有拒绝）；
   - SQLite 中存在对应 `request_id` 的 `intent_log` 与 `governor_log` 记录；
   - 响应不含伪执行宣告（`FALSE_EXECUTION_CLAIM` 检查）。

**测试场景**（T-E2E-02）：执行链路 Smoke（有 approval_token）：
1. 发送执行 intent（`action=desktop_note`，`risk_level=2`）；
2. 断言：Governor APPROVE → approval_token 签发 → skill_exec_log 写入 → manifest_ref 存在；
3. 发送 undo（携带 undo_token）→ 断言文件恢复 + vault_gc_log 不删有效 undo 窗口材料。

**测试场景**（T-E2E-03）：故障注入矩阵（NFR-REL-001）：
- 注入 Neo4j 不可用 → 输出结构稳定，`reason_code=DEPENDENCY_UNAVAILABLE`；
- 注入 Chroma 不可用 → 输出结构稳定，evidence pack 降级但字段存在；
- 注入 Cloud LLM 超时 → 输出最小模板，不产生执行 intent；
- 注入 Edge RPC 不可达 → 语音链路降级，`ASR_UNAVAILABLE` 可统计。

**验收测试编号**：T-E2E-01~03

---

### 18.3 性能基线采集（TASK-0109）

#### 18.3.1 p50/p95 响应时延基线（NFR-PERF-001）

**采集方式**：在 `host_brain/obs/metrics.py` 的 `MetricsCollector` 中，从 `retrieval_event_log` 聚合检索链路时延；从 `governor_log` 聚合裁决时延；从 `skill_exec_log` 聚合执行时延。

**基线指标**（本地基准机器，无外部依赖）：

| 链路段 | p50 目标 | p95 目标 |
|--------|---------|---------|
| 检索链路（embedding + rerank + fusion） | ≤ 200ms | ≤ 500ms |
| Governor 裁决（含 rule_trace） | ≤ 20ms | ≤ 50ms |
| Skill 执行（desktop_note，本地文件） | ≤ 100ms | ≤ 300ms |
| 完整对话链路（无 LLM，降级模式） | ≤ 500ms | ≤ 1500ms |

**输出**：`/obs/metrics` 响应中新增 `latency_baseline` 字段：
```json
{
  "latency_baseline": {
    "retrieval_p50_ms": 180,
    "retrieval_p95_ms": 420,
    "governor_p50_ms": 12,
    "governor_p95_ms": 38,
    "skill_exec_p50_ms": 85,
    "skill_exec_p95_ms": 210,
    "sampled_at": "2026-03-03T12:00:00Z",
    "sample_count": 100
  }
}
```

**验收测试编号**：T-PERF-01（基线字段结构稳定 + 采样计数 > 0 + 时延值合理范围）

---

### 18.4 发布清单与版本锁定（TASK-0110）

#### 18.4.1 M8 发布清单（release manifest）

**目标**：在 `host_brain/release/release_manifest.py` 基础上，生成 M8 发布清单，锁定：
- 当前 git commit hash；
- 所有 migration 文件的 hash（SQLite + Neo4j）；
- 测试通过数（914 + M8 新增）；
- 性能基线快照（来自 T-PERF-01）；
- 服务注册表快照（所有 required=true 服务的 status）。

**输出文件**：`release/manifests/m8_release.json`

**验收测试编号**：T-REL-01（发布清单字段完整性 + hash 可验证）

---

### 18.5 M8 新增错误码汇总

- `STARTUP_OBS_DEGRADED`：启动时 OBS 子系统不可用
- `STARTUP_MCP_DEGRADED`：启动时 MCP 注册表不可用
- `E2E_SMOKE_FAILED`：端到端 Smoke 测试失败（仅用于 CI 报告）
- `PERF_BASELINE_STALE`：性能基线采样数据过期（超过 24h 未更新）


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

---

## §20 M10-A Canon 治理 API（FR-WCANON-002 扩展）

> 对应 PRD M10-A（P0）：Canon 提案审核 REST API。
> 将 M9 已落地的 `submit_proposal()` / `approve_proposal_with_content()` 暴露为可操作的 REST 端点，
> 支持 Master 通过 HTTP 接口查询、提交、审核、拒绝 World Canon Evolution 提案。

---

### 20.1 设计原则

1. **Host 主权不变**：所有写操作仍经 API-level 权限门控（`proposer_node_id` 强制标注来源），不引入旁路。
2. **审计串联**：每个 API 调用产生 `request_id`，写操作必须将 `request_id` 写入 `canon_proposal_log.proposer_node_id`（格式：`api.canon_proposals::{request_id}`）。
3. **幂等性**：`POST /canon/proposals`（提交提案）对相同 `proposal_id` 幂等（重复提交返回 409 + WCANON_PROPOSAL_DUPLICATE，不重复写入）。
4. **结构稳定**：成功/拒绝/降级均返回统一 JSON 结构（`CanonProposalResponse`），含 `reason_code`。
5. **只读端点无副作用**：`GET /canon/proposals` / `GET /canon/proposals/{proposal_id}` / `GET /canon/documents` 不修改任何状态。

---

### 20.2 REST 端点规格

#### 20.2.1 端点清单

| 方法 | 路径 | 描述 | 副作用 |
|------|------|------|--------|
| GET | `/canon/proposals` | 列出提案（分页，可按 status 过滤） | 无 |
| POST | `/canon/proposals` | 提交新提案（status=pending） | 写 canon_proposal_log |
| GET | `/canon/proposals/{proposal_id}` | 查询单个提案详情 | 无 |
| POST | `/canon/proposals/{proposal_id}/approve` | 审核通过提案（触发 SSOT 写入） | 写 SSOT + canon_change_log |
| POST | `/canon/proposals/{proposal_id}/reject` | 拒绝提案（status=rejected，不写 SSOT） | 写 canon_proposal_log（status 更新） |
| GET | `/canon/documents` | 列出当前 World Canon 文档（SSOT 快照） | 无 |
| GET | `/canon/documents/{doc_id}` | 查询单个 World Canon 文档 | 无 |

#### 20.2.2 请求/响应 Schema

**CanonProposalSubmitRequest**（POST /canon/proposals）：
```json
{
  "proposal_id": "string（调用方生成，唯一）",
  "target_doc_id": "string（目标 World Canon 文档 ID）",
  "proposed_title": "string",
  "proposed_tags": ["string"],
  "proposed_body": "string（不得含完成性表述）",
  "proposer_node_id": "string（可选，默认 api.canon_proposals）",
  "summary": "string（可选，不超过 200 字）"
}
```

**CanonProposalApproveRequest**（POST /canon/proposals/{id}/approve）：
```json
{
  "proposed_title": "string",
  "proposed_tags": ["string"],
  "proposed_body": "string",
  "approver_node_id": "string（可选，默认 api.canon_proposals.approve）"
}
```

**CanonProposalRejectRequest**（POST /canon/proposals/{id}/reject）：
```json
{
  "reason": "string（拒绝原因，不超过 200 字）",
  "rejector_node_id": "string（可选）"
}
```

**CanonProposalResponse**（统一响应结构）：
```json
{
  "proposal_id": "string",
  "target_doc_id": "string",
  "status": "pending | approved | rejected | APPLIED",
  "proposer_node_id": "string",
  "summary": "string",
  "proposed_hash": "string",
  "base_version": "int",
  "proposed_version": "int",
  "ts_ms": "int",
  "request_id": "string",
  "reason_code": "string | null",
  "timestamp": "int"
}
```

**CanonDocumentResponse**（World Canon 文档响应）：
```json
{
  "doc_id": "string",
  "version": "int",
  "hash": "string",
  "status": "string",
  "title": "string",
  "tags": ["string"],
  "body": "string",
  "updated_by": "string",
  "ts_ms": "int",
  "source_path": "string",
  "request_id": "string",
  "timestamp": "int"
}
```

**CanonProposalListResponse**（列表响应）：
```json
{
  "items": ["CanonProposalResponse"],
  "total": "int",
  "page": "int",
  "page_size": "int",
  "request_id": "string",
  "timestamp": "int"
}
```

#### 20.2.3 错误码映射

| HTTP 状态码 | reason_code | 触发场景 |
|------------|-------------|---------|
| 400 | WCANON_PROPOSAL_INVALID | 请求字段不合规（缺必填字段，或 body 含完成性表述） |
| 404 | WCANON_PROPOSAL_NOT_FOUND | 提案 ID 不存在 |
| 404 | WCANON_DOC_NOT_FOUND | World Canon 文档 ID 不存在 |
| 409 | WCANON_PROPOSAL_DUPLICATE | 相同 proposal_id 已存在（幂等返回已有记录） |
| 422 | WCANON_PROPOSAL_STATUS_INVALID | 提案 status 不是 pending（不可审核/拒绝） |
| 500 | CANON_SYNC_FAILED | SSOT 文件写入失败 |

---

### 20.3 路由注册方式

- 新建 `host_brain/app/canon_routes.py`，使用 `fastapi.APIRouter(prefix="/canon", tags=["canon"])`；
- 在 `fastapi_app.py` 中 `app.include_router(canon_router)` 注册（路由自带 `/canon` 前缀）；
- 路由依赖 `WorldCanonStore`（通过 `get_canon_store()` 依赖注入，延迟初始化，`world_root` 从环境变量 `Natsume_WORLD_CANON_ROOT` 读取，默认 `host_brain/canon/world`）；
- 路由依赖 `SQLiteLedger`（复用 `get_ledger()`）。

---

### 20.4 审计串联要求

- 每个写操作（POST /canon/proposals、approve、reject）必须：
  - 生成 `request_id`（`new_request_id()`）；
  - 将 `request_id` 写入 `canon_proposal_log.proposer_node_id`（格式：`api.canon_proposals::{request_id}`）；
  - approve 时写 `canon_change_log`（通过 `upsert_document()` 内部完成）；
  - reject 时写 `system_event_log`（event_type=WCANON_PROPOSAL_REJECTED，含 proposal_id + reason）。
- 只读操作（GET）不写审计，但必须在响应中携带 `request_id`。

---

### 20.5 `reject_proposal()` 方法扩展（canon_world_store.py）

M9 的 `WorldCanonStore` 缺少 `reject_proposal()` 方法，M10-A 需要补充：

```python
def reject_proposal(
    self,
    proposal_id: str,
    *,
    reason: str = "",
    rejector_node_id: str = "api.canon_proposals.reject",
    ts_ms: int | None = None,
) -> dict[str, Any]:
    reject_ts = int(ts_ms if ts_ms is not None else now_ms())
    proposal = self.ledger.get_proposal(proposal_id)
    if proposal is None:
        raise KeyError(f"proposal not found: {proposal_id}")
    current_status = str(proposal.get("status", ""))
    if current_status != "pending":
        raise PermissionError(
            f"proposal {proposal_id!r} cannot be rejected: "
            f"current status is {current_status!r}, expected 'pending'"
        )
    self.ledger.update_proposal_status(proposal_id, new_status="rejected", ts_ms=reject_ts)
    return {**proposal, "status": "rejected", "ts_ms": reject_ts, "reject_reason": reason}
```

---

### 20.6 回归测试编号（M10-A 新增）

| 编号 | 覆盖需求 | 断言口径 |
|------|---------|---------|
| T-Canon-API-01 | FR-WCANON-002 | POST /canon/proposals 提交成功，status=pending，proposal_id 可查询 |
| T-Canon-API-02 | FR-WCANON-002 | POST /canon/proposals 重复 proposal_id 返回 409 + WCANON_PROPOSAL_DUPLICATE |
| T-Canon-API-03 | FR-WCANON-002 | POST /canon/proposals body 含完成性表述返回 400 + WCANON_PROPOSAL_INVALID |
| T-Canon-API-04 | FR-WCANON-002 | POST /canon/proposals/{id}/approve 审核通过，SSOT 文件更新，canon_change_log 可回放 |
| T-Canon-API-05 | FR-WCANON-002 | POST /canon/proposals/{id}/approve 对非 pending 提案返回 422 + WCANON_PROPOSAL_STATUS_INVALID |
| T-Canon-API-06 | FR-WCANON-002 | POST /canon/proposals/{id}/reject 拒绝成功，status=rejected，SSOT 不变 |
| T-Canon-API-07 | FR-WCANON-002 | GET /canon/proposals 分页列表，status 过滤正确 |
| T-Canon-API-08 | FR-WCANON-001 | GET /canon/documents 返回当前 SSOT 快照（3 个种子条目） |

---

### 20.7 M10-A 新增错误码汇总

| 错误码 | 触发场景 |
|--------|---------|
| WCANON_PROPOSAL_NOT_FOUND | 提案 ID 不存在 |
| WCANON_DOC_NOT_FOUND | World Canon 文档 ID 不存在 |
| WCANON_PROPOSAL_DUPLICATE | 相同 proposal_id 已存在 |
| WCANON_PROPOSAL_STATUS_INVALID | 提案 status 不允许当前操作（非 pending） |

---

## §21 定时调度器（Scheduler）工程契约

### 21.1 模块边界

```
host_brain/scheduler/
├── __init__.py
├── scheduler_engine.py      # SchedulerEngine：任务注册/注销/触发/健康状态
├── scheduler_models.py      # ScheduledTask、TaskHealth、SchedulerConfig Schema
├── scheduler_store.py       # SQLite 持久化（scheduler_tasks 表）
└── scheduler_routes.py      # FastAPI 路由：/scheduler/tasks CRUD + /scheduler/health
```

### 21.2 核心 Schema

#### ScheduledTask
```python
@dataclass
class ScheduledTask:
    task_id: str                    # UUID，唯一标识
    name: str                       # 任务名称（可读）
    cron_expr: str | None           # cron 表达式（如 "0 23 * * *"）
    interval_seconds: int | None    # 周期触发（秒），与 cron_expr 二选一
    intent_type: str                # 触发的 intent type（allowlist 校验）
    intent_action: str              # 触发的 intent action
    intent_args: dict               # 触发时附带的 args
    sys_mode_allowlist: list[str]   # 允许触发的 sys_mode（如 ["NORMAL", "SLEEP"]）
    priority: str                   # "HIGH" / "NORMAL" / "LOW"
    enabled: bool                   # 是否启用
    created_at: str                 # ISO8601
    updated_at: str                 # ISO8601
    config_version: int             # 配置版本号（每次更新递增）
```

#### TaskHealth
```python
@dataclass
class TaskHealth:
    task_id: str
    status: str                     # "RUNNING" / "PAUSED" / "SUSPENDED" / "DISABLED"
    last_run_ts: str | None         # 上次触发时间
    next_run_ts: str | None         # 下次预计触发时间
    last_result: str | None         # "SUCCESS" / "FAILED" / "SKIPPED"
    error_count: int                # 连续失败次数
    suspended_reason: str | None    # 暂停原因（如 "SCHED_TASK_SUSPENDED"）
```

### 21.3 SQLite 表：scheduler_tasks

```sql
CREATE TABLE IF NOT EXISTS scheduler_tasks (
    task_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    cron_expr       TEXT,
    interval_seconds INTEGER,
    intent_type     TEXT NOT NULL,
    intent_action   TEXT NOT NULL,
    intent_args     TEXT NOT NULL DEFAULT '{}',   -- JSON
    sys_mode_allowlist TEXT NOT NULL DEFAULT '["NORMAL"]',  -- JSON array
    priority        TEXT NOT NULL DEFAULT 'NORMAL',
    enabled         INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'RUNNING',
    last_run_ts     TEXT,
    next_run_ts     TEXT,
    last_result     TEXT,
    error_count     INTEGER NOT NULL DEFAULT 0,
    suspended_reason TEXT,
    config_version  INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
```

### 21.4 SchedulerEngine 接口契约

```python
class SchedulerEngine:
    def register_task(self, task: ScheduledTask) -> None:
        """注册任务；task_id 重复则 raise SCHED_TASK_DUPLICATE"""

    def unregister_task(self, task_id: str) -> None:
        """注销任务；不存在则 raise SCHED_TASK_NOT_FOUND"""

    def enable_task(self, task_id: str) -> None: ...
    def disable_task(self, task_id: str) -> None: ...

    def get_health(self, task_id: str) -> TaskHealth: ...
    def list_health(self) -> list[TaskHealth]: ...

    def tick(self, current_ts: float, current_sys_mode: str) -> list[str]:
        """
        调度心跳：检查所有 enabled 任务，返回本次触发的 task_id 列表。
        - sys_mode 不在 allowlist → SKIPPED（不触发，不计入 error_count）
        - 触发后生成 system_initiated_intent 并推入 intent_queue
        - 连续失败 MAX_ERROR_COUNT 次 → status=SUSPENDED + 写 scheduler_config_log
        """

    def _build_system_intent(self, task: ScheduledTask) -> dict:
        """
        构造 system_initiated_intent：
        - proposer_node_id = "scheduler"
        - intent_id = uuid4()
        - type = task.intent_type
        - action = task.intent_action
        - args = task.intent_args
        - source_task_id = task.task_id
        """
```

### 21.5 sys_mode 门控规则

| sys_mode | HIGH 优先级任务 | NORMAL 优先级任务 | LOW 优先级任务 |
|---------|--------------|----------------|--------------|
| NORMAL  | ✅ 触发 | ✅ 触发 | ✅ 触发 |
| GAMING  | ✅ 触发 | ❌ SYSMODE_FORBIDDEN | ❌ SYSMODE_FORBIDDEN |
| SLEEP   | ✅ 触发（白名单） | ❌ SYSMODE_FORBIDDEN | ❌ SYSMODE_FORBIDDEN |
| FOCUS   | ✅ 触发 | ❌ SYSMODE_FORBIDDEN（proactive/dream 类） | ❌ SYSMODE_FORBIDDEN |

> SLEEP 模式下 HIGH 优先级白名单：`["diary_generate", "dream_generate"]`

### 21.6 REST API 端点

| 方法 | 路径 | 描述 |
|------|------|------|
| POST | /scheduler/tasks | 注册新任务 |
| GET  | /scheduler/tasks | 列出所有任务（含健康状态） |
| GET  | /scheduler/tasks/{task_id} | 获取单个任务详情 |
| PATCH | /scheduler/tasks/{task_id} | 更新任务配置（config_version 递增） |
| DELETE | /scheduler/tasks/{task_id} | 注销任务 |
| POST | /scheduler/tasks/{task_id}/enable | 启用任务 |
| POST | /scheduler/tasks/{task_id}/disable | 禁用任务 |
| GET  | /scheduler/health | 所有任务健康快照 |

### 21.7 审计字段

- `scheduler_config_log`：任务注册/注销/配置变更/暂停事件，字段：`task_id / event_type / config_version / reason / ts`
- `system_initiated_intent`：调度触发的 intent，字段：`proposer_node_id="scheduler" / source_task_id / intent_id / ts`

### 21.8 错误码

| 错误码 | 触发场景 |
|--------|---------|
| SCHED_TASK_NOT_FOUND | task_id 不存在 |
| SCHED_TASK_DUPLICATE | task_id 已存在 |
| SCHED_TASK_SUSPENDED | 连续失败超限，任务自动暂停 |
| SCHED_CONDITION_UNMET | 触发条件不满足（非 sys_mode 原因） |
| SCHED_CONFIG_INVALID | cron_expr/interval_seconds 格式错误或两者均为空 |

### 21.9 测试编号

| 测试编号 | 关联需求 | 断言描述 |
|---------|---------|---------|
| T-SCHED-01 | FR-SCHED-001 | 注册任务后 tick() 在正确时间触发，生成 system_initiated_intent |
| T-SCHED-02 | FR-SCHED-001 | sys_mode=GAMING 时 NORMAL 优先级任务被跳过（SYSMODE_FORBIDDEN） |
| T-SCHED-03 | FR-SCHED-001 | sys_mode=SLEEP 时 HIGH 优先级白名单任务正常触发 |
| T-SCHED-04 | FR-SCHED-002 | 连续失败 MAX_ERROR_COUNT 次后 status=SUSPENDED，写 scheduler_config_log |
| T-SCHED-05 | FR-SCHED-002 | /scheduler/health 返回所有任务健康快照，字段完整 |
| T-SCHED-06 | FR-SCHED-001 | 注销任务后 tick() 不再触发 |
| T-SCHED-07 | FR-SCHED-001 | system_initiated_intent 中 proposer_node_id="scheduler" 且 source_task_id 正确 |

---

## §22 桌面留言（Desktop Message）工程契约

### 22.1 模块边界

```
host_brain/skills/desktop_message/
├── __init__.py
├── desktop_message_skill.py    # DesktopMessageSkill：create/update/delete/undo
├── desktop_message_models.py   # DesktopMessageArgs、DesktopMessageManifest Schema
└── path_validator.py           # 安全路径校验（base_dir allowlist + 路径穿越检测）
```

### 22.2 SkillMeta 声明

```python
DESKTOP_MESSAGE_SKILL_META = SkillMeta(
    skill_name="desktop_message",
    clearance_level="L1",
    risk_level=2,
    scopes=["filesystem"],
    reversible=True,
    requires_visual_audit=False,
    requires_confirm_token=False,   # 低风险，不需要二次确认
    allow_list=["create", "update", "delete", "undo"],
    deny_list=[],
)
```

### 22.3 核心 Schema

#### DesktopMessageArgs
```python
@dataclass
class DesktopMessageArgs:
    action: str          # "create" / "update" / "delete" / "undo"
    filename: str        # 文件名（不含路径，仅文件名部分）
    content: str | None  # 留言内容（create/update 时必填）
    undo_token: str | None  # undo 时必填
```

#### DesktopMessageManifest
```python
@dataclass
class DesktopMessageManifest:
    manifest_id: str
    skill_name: str = "desktop_message"
    action: str
    file_path: str          # 绝对路径（已校验）
    before_hash: str | None # 操作前文件 SHA256（不存在时为 None）
    after_hash: str | None  # 操作后文件 SHA256（删除时为 None）
    before_content: str | None
    after_content: str | None
    undo_token: str
    reversible: bool = True
    ts: str                 # ISO8601
    request_id: str
    intent_id: str
```

### 22.4 安全路径校验规则

```python
BASE_DIR_ALLOWLIST = [
    Path.home() / "Desktop",
    Path.home() / "Documents" / "Natsume_Messages",
]

def validate_path(filename: str, base_dir: Path) -> Path:
    """
    1. 拒绝包含 '..' 的 filename → SCOPE_VIOLATION
    2. 拒绝绝对路径 filename → SCOPE_VIOLATION
    3. 拒绝符号链接 → SCOPE_VIOLATION
    4. 解析后路径必须以 base_dir 开头 → SCOPE_VIOLATION
    5. 文件名长度 ≤ 255 字节 → SCOPE_VIOLATION
    """
```

### 22.5 执行流程

```
create/update:
  1. validate_path(filename, base_dir)
  2. 读取 before_content（文件不存在时 before_hash=None）
  3. QA 检查 content（不得含伪执行句）
  4. 写入文件
  5. 计算 after_hash
  6. 生成 manifest + undo_token
  7. 写入 skill_exec_log（含 manifest_ref）

delete:
  1. validate_path(filename, base_dir)
  2. 读取 before_content + before_hash
  3. move-to-quarantine（不真删）
  4. 生成 manifest（after_hash=None）+ undo_token
  5. 写入 skill_exec_log

undo:
  1. 校验 undo_token（TTL + manifest_ref 匹配）
  2. 读取 manifest（before_content/before_hash）
  3. 恢复文件（create→删除；update→写回 before_content；delete→从 quarantine 恢复）
  4. 写入 skill_exec_log（undo 事件）
```

### 22.6 错误码

| 错误码 | 触发场景 |
|--------|---------|
| SCOPE_VIOLATION | 路径越界/路径穿越/符号链接 |
| SYSMODE_FORBIDDEN | Gaming 模式下触发 |
| SKILL_EXEC_FAILED | 文件系统不可写/IO 错误 |
| DESKTOP_MSG_QA_REJECTED | 内容含伪执行句，QA 拦截 |
| DESKTOP_MSG_UNDO_INVALID | undo_token 无效/过期/manifest 不匹配 |
| DESKTOP_MSG_NOT_FOUND | delete/update 时文件不存在 |

### 22.7 测试编号

| 测试编号 | 关联需求 | 断言描述 |
|---------|---------|---------|
| T-DESK-01 | FR-DESKTOP-001 | create 成功，manifest 含 before(None)/after/path/hash |
| T-DESK-02 | FR-DESKTOP-001 | undo_token 调用后文件恢复原状态 |
| T-DESK-03 | FR-DESKTOP-001 | 路径越界（含 ..）→ SCOPE_VIOLATION |
| T-DESK-04 | FR-DESKTOP-001 | Gaming 模式 → SYSMODE_FORBIDDEN |
| T-DESK-05 | FR-DESKTOP-001 | 文件系统不可写 → SKILL_EXEC_FAILED + reason_code |
| T-DESK-06 | FR-DESKTOP-002 | 内容含伪执行句 → DESKTOP_MSG_QA_REJECTED |
| T-DESK-07 | FR-DESKTOP-001 | delete 默认 move-to-quarantine，undo 可恢复 |
| T-DESK-08 | FR-DESKTOP-002 | 写入审计可追溯原始草案与最终内容 |

---

## §23 梦境/日记/主动对话/Focus Mode 工程契约

### 23.1 梦境（Dream）模块边界

```
host_brain/skills/dream/
├── __init__.py
├── dream_skill.py          # DreamSkill：generate（读 Episodic → LLM → 写 Episodic）
└── dream_models.py         # DreamArgs、DreamEntry Schema
```

#### DreamEntry Schema
```python
@dataclass
class DreamEntry:
    entry_id: str
    category: str = "dream"
    evidence_level: str = "NARRATIVE_ONLY"
    content: str                    # LLM 生成的梦境叙事
    source_episodic_ids: list[str]  # 来源 Episodic 条目 ID
    ts: str                         # ISO8601
    request_id: str
    intent_id: str
    reversible: bool = True
    undo_token: str | None = None
```

#### 触发条件校验
```python
def check_dream_precondition(sys_mode: str, idle_duration_s: float, config: dict) -> bool:
    """
    sys_mode in ["SLEEP"] OR idle_duration_s >= config["idle_threshold_s"]
    AND sys_mode NOT IN ["GAMING"]
    """
```

#### 测试编号

| 测试编号 | 关联需求 | 断言描述 |
|---------|---------|---------|
| T-DREAM-01 | FR-DREAM-001 | Sleep 模式触发，梦境写入 Episodic，category=dream |
| T-DREAM-02 | FR-DREAM-001 | Normal 模式无空闲 → SCHED_CONDITION_UNMET |
| T-DREAM-03 | FR-DREAM-001 | Episodic 不可用 → DEPENDENCY_UNAVAILABLE，不伪造 |
| T-DREAM-04 | FR-DREAM-002 | 召回梦境时 evidence_level=NARRATIVE_ONLY 标注存在 |
| T-DREAM-05 | FR-DREAM-001 | Gaming 模式 → SYSMODE_FORBIDDEN |

---

### 23.2 日记（Diary）模块边界

```
host_brain/skills/diary/
├── __init__.py
├── diary_skill.py          # DiarySkill：generate（读当日对话 → LLM → 写文件 + Episodic）
└── diary_models.py         # DiaryArgs、DiaryEntry、DiaryManifest Schema
```

#### DiaryManifest Schema
```python
@dataclass
class DiaryManifest:
    manifest_id: str
    skill_name: str = "diary"
    date: str                       # YYYY-MM-DD
    file_path: str                  # 绝对路径（安全基目录内）
    before_hash: str | None
    after_hash: str
    episodic_entry_ids: list[str]   # 写入的 Episodic 条目 ID
    canon_proposal_ids: list[str]   # 可选：关联的 canon_proposal（FR-DIARY-002）
    undo_token: str
    reversible: bool = True
    ts: str
    request_id: str
    intent_id: str
```

#### 测试编号

| 测试编号 | 关联需求 | 断言描述 |
|---------|---------|---------|
| T-DIARY-01 | FR-DIARY-001 | 每日 23:00 触发，日记文件创建成功，Episodic 写入成功 |
| T-DIARY-02 | FR-DIARY-001 | 无当日对话 → 输出"无内容"，不伪造日记 |
| T-DIARY-03 | FR-DIARY-001 | 文件系统不可用 → SKILL_EXEC_FAILED，不伪造 |
| T-DIARY-04 | FR-DIARY-001 | Episodic 不可用 → DEPENDENCY_UNAVAILABLE，文件仍可写入（降级） |
| T-DIARY-05 | FR-DIARY-002 | 日记含设定漂移建议时，生成 canon_proposal 并写入 canon_proposal_log |

---

### 23.3 主动对话（Proactive）模块边界

```
host_brain/skills/proactive/
├── __init__.py
├── proactive_skill.py      # ProactiveSkill：trigger（条件校验 → LLM 生成 → 推送）
└── proactive_models.py     # ProactiveArgs、ProactiveEvent Schema
```

#### 触发条件校验
```python
def check_proactive_precondition(
    idle_duration_s: float,
    bond_stage: str,
    sys_mode: str,
    hourly_count: int,
    config: dict,
) -> tuple[bool, str | None]:
    """
    Returns (can_trigger, reason_code_if_blocked)
    - sys_mode == "GAMING" → (False, "SYSMODE_FORBIDDEN")
    - hourly_count >= config["max_per_hour"] → (False, "RATE_LIMIT_EXCEEDED")
    - idle_duration_s < config["idle_threshold_s"] → (False, "SCHED_CONDITION_UNMET")
    - bond_stage not in config["bond_stage_allowlist"] → (False, "SCHED_CONDITION_UNMET")
    """
```

#### 测试编号

| 测试编号 | 关联需求 | 断言描述 |
|---------|---------|---------|
| T-PROACT-01 | FR-PROACTIVE-001 | 空闲触发，用户端收到主动消息，审计链完整 |
| T-PROACT-02 | FR-PROACTIVE-001 | Gaming → SYSMODE_FORBIDDEN |
| T-PROACT-03 | FR-PROACTIVE-001 | 频率超限 → RATE_LIMIT_EXCEEDED |
| T-PROACT-04 | FR-PROACTIVE-002 | 内容含执行 intent → QA 拦截 |
| T-PROACT-05 | FR-PROACTIVE-002 | Edge 不可达 → 静默降级，不重试（DEPENDENCY_UNAVAILABLE） |
| T-PROACT-06 | FR-PROACTIVE-001 | Sleep 模式 → 仅允许低频问候（bond_stage 白名单） |

---

### 23.4 Focus Mode 工程契约

#### sys_mode 扩展

Focus Mode 作为 `sys_mode` 的子状态，在 `SysModeEnum` 中新增 `FOCUS`：

```python
class SysModeEnum(str, Enum):
    NORMAL = "NORMAL"
    GAMING = "GAMING"
    SLEEP = "SLEEP"
    FOCUS = "FOCUS"   # 新增
```

#### 行为矩阵

| 能力 | FOCUS 模式行为 |
|------|--------------|
| 用户主动对话 | ✅ 正常响应 |
| 主动对话（Proactive） | ❌ SYSMODE_FORBIDDEN |
| 梦境生成（Dream） | ❌ SYSMODE_FORBIDDEN |
| 日记生成（Diary） | ❌ SYSMODE_FORBIDDEN（推迟到退出 Focus 后） |
| 通知推送 | ❌ 静默 |
| Scheduler 非紧急任务 | ❌ SYSMODE_FORBIDDEN |

#### 切换审计

```python
# hard_state_log 字段
{
    "event_type": "SYS_MODE_CHANGE",
    "from_mode": "NORMAL",
    "to_mode": "FOCUS",
    "source": "user_command" | "scheduled_policy" | "calendar_event",
    "evidence_summary": str,
    "ts": str,
    "request_id": str,
}
```

#### 测试编号

| 测试编号 | 关联需求 | 断言描述 |
|---------|---------|---------|
| T-FOCUS-01 | FR-FOCUS-001 | 进入 Focus 后主动对话被拦截（SYSMODE_FORBIDDEN） |
| T-FOCUS-02 | FR-FOCUS-001 | 用户主动提问正常响应 |
| T-FOCUS-03 | FR-FOCUS-001 | 退出 Focus 后调度器恢复 |
| T-FOCUS-04 | FR-FOCUS-001 | 切换事件写入 hard_state_log，字段完整 |

### 23.5 §21~§23 新增错误码汇总

| 错误码 | 触发场景 |
|--------|---------|
| SCHED_TASK_NOT_FOUND | 调度任务 ID 不存在 |
| SCHED_TASK_DUPLICATE | 调度任务 ID 已存在 |
| SCHED_TASK_SUSPENDED | 连续失败超限，任务自动暂停 |
| SCHED_CONDITION_UNMET | 触发条件不满足（非 sys_mode 原因） |
| SCHED_CONFIG_INVALID | cron_expr/interval_seconds 格式错误 |
| SCOPE_VIOLATION | 路径越界/路径穿越/符号链接 |
| DESKTOP_MSG_QA_REJECTED | 桌面留言内容含伪执行句 |
| DESKTOP_MSG_UNDO_INVALID | undo_token 无效/过期/manifest 不匹配 |
| DESKTOP_MSG_NOT_FOUND | 目标文件不存在 |
| RATE_LIMIT_EXCEEDED | 主动对话频率超限 |

---

## §24 M12 工程契约（PLANNED）

### §24.1 M12-A ToolBroker 单入口 + tool_visibility_hash（FR-TOOL-001/002/003）

#### 24.1.1 模块边界

```
host_brain/tools/
  tool_broker.py          ← ToolBroker 单入口（NEW）
  tool_meta_registry.py   ← ToolMeta 注册表（NEW）
  tool_visibility.py      ← tool_visibility_hash 计算与审计（NEW）
```

#### 24.1.2 ToolMeta Schema

```python
@dataclass(frozen=True)
class ToolMeta:
    tool_id: str                    # 唯一标识
    provider_id: str                # MCP provider 或 internal
    name: str                       # 展示名
    description: str                # 功能描述（暴露给 LLM）
    access_level: Literal["READ", "WRITE"]
    requires_token: bool            # WRITE 必须为 True
    clearance_level: int            # 0~4
    risk_level: int                 # 0~4
    scopes: list[str]
    reversible: bool
    version: str
```

#### 24.1.3 ToolBroker 接口

```python
class ToolBroker:
    def get_tool_meta_pack(
        self,
        context: dict,
        top_k: int = 5,
    ) -> ToolMetaPack:
        """返回当前轮次可见的 Top-K 工具元数据（不含 WRITE 默认）。"""

    def execute_tool(
        self,
        tool_id: str,
        args: dict,
        context: dict,
    ) -> ToolExecResult:
        """执行工具（WRITE 必须有 approval_token）。"""

    def compute_visibility_hash(
        self,
        tool_meta_pack: ToolMetaPack,
    ) -> str:
        """计算 tool_visibility_hash（SHA-256 of sorted tool_ids）。"""
```

#### 24.1.4 tool_visibility_hash 审计字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `tool_visibility_hash` | str | SHA-256(sorted tool_ids) |
| `tool_count` | int | 本轮可见工具数量 |
| `write_tools_visible` | bool | 是否包含 WRITE 工具 |
| `request_id` | str | 关联请求 |
| `intent_id` | str | 关联 intent |
| `ts_ms` | int | 时间戳 |

#### 24.1.5 错误码

| 错误码 | 触发条件 |
|--------|---------|
| `TOOL_VISIBILITY_POLICY_VIOLATION` | WRITE 工具出现在未授权上下文 |
| `TOOL_TOKEN_MISSING` | WRITE 工具无 approval_token |
| `TOOL_NOT_FOUND` | tool_id 不在注册表 |
| `TOOL_EXEC_FAILED` | 工具执行异常 |
| `TOOL_EVIDENCE_REF_MISSING` | WRITE 执行无 evidence_ref |

#### 24.1.6 测试编号

| 编号 | 描述 |
|------|------|
| T-TOOL-01 | get_tool_meta_pack 默认不含 WRITE 工具 |
| T-TOOL-02 | WRITE 工具无 token → TOOL_TOKEN_MISSING |
| T-TOOL-03 | tool_visibility_hash 可重现（同输入同输出） |
| T-TOOL-04 | tool_visibility_hash 写入审计链 |
| T-TOOL-05 | MCP WRITE 无 APPROVE → 拒绝且可统计 |
| T-TOOL-06 | 绕过 ToolBroker 直接执行 → 阻断 |

---

### §24.2 M12-B 多轮对话上下文压缩（FR-MEM-005 扩展）

#### 24.2.1 模块边界

```
host_brain/memory/
  context_compressor.py   ← ContextCompressor（NEW）
```

#### 24.2.2 ContextCompressor 接口

```python
class ContextCompressor:
    def compress(
        self,
        turns: list[dict],
        *,
        max_tokens: int = 2048,
        strategy: Literal["sliding_window", "summary", "hybrid"] = "hybrid",
    ) -> CompressedContext:
        """压缩多轮对话上下文，返回结构化 evidence pack。"""

    def build_evidence_pack(
        self,
        compressed: CompressedContext,
        episodic_hits: list[dict],
        canon_slots: dict,
    ) -> EvidencePack:
        """构建结构化 evidence pack（候选/分数/摘要/来源指针/置信度）。"""
```

#### 24.2.3 EvidencePack Schema

```python
@dataclass(frozen=True)
class EvidencePack:
    pack_id: str
    request_id: str
    intent_id: str
    candidates: list[EvidenceCandidate]   # TopK 候选
    total_tokens: int
    strategy: str
    ts_ms: int
    hash: str                              # SHA-256(candidates)
```

#### 24.2.4 测试编号

| 编号 | 描述 |
|------|------|
| T-CTX-01 | sliding_window 截断超出 max_tokens 的轮次 |
| T-CTX-02 | summary 策略生成摘要且不伪造事实 |
| T-CTX-03 | hybrid 策略优先保留最近 N 轮 + 摘要历史 |
| T-CTX-04 | evidence_pack 字段结构稳定（同版本同输入） |
| T-CTX-05 | LLM 不可用时降级为 sliding_window |

---

### §24.3 M12-C Reranker 服务强化（FR-MEM-004）

#### 24.3.1 模块边界

```
host_brain/retrieval/
  reranker.py             ← RerankerService（已有，需强化）
```

#### 24.3.2 RerankerService 接口强化

```python
class RerankerService:
    def rerank(
        self,
        query: str,
        candidates: list[dict],
        *,
        top_k: int = 5,
        fallback: Literal["recall_order", "lightweight"] = "recall_order",
    ) -> RerankResult:
        """重排候选，不可用时降级并显式标注 stage。"""

    def health(self) -> dict:
        """返回健康状态（status/latency_ms/last_error）。"""
```

#### 24.3.3 测试编号

| 编号 | 描述 |
|------|------|
| T-RERANK-01 | 正常重排返回 TopK，字段稳定 |
| T-RERANK-02 | Reranker 不可用 → 降级为 recall_order，stage 标注 |
| T-RERANK-03 | health() 返回结构稳定 |
| T-RERANK-04 | evidence_pack 含 rerank_stage 字段 |

---

### §24.4 M12-D 技能热加载（FR-SKILL-001/002 扩展）

#### 24.4.1 模块边界

```
host_brain/skills/
  hot_loader.py           ← SkillHotLoader（NEW）
  version_registry.py     ← SkillVersionRegistry（NEW）
```

#### 24.4.2 SkillHotLoader 接口

```python
class SkillHotLoader:
    def reload_skill(
        self,
        skill_name: str,
        *,
        version: str | None = None,
    ) -> SkillReloadResult:
        """热加载指定 Skill（不重启服务）。"""

    def list_versions(self, skill_name: str) -> list[SkillVersion]:
        """列出已注册的 Skill 版本。"""

    def rollback_skill(
        self,
        skill_name: str,
        target_version: str,
    ) -> SkillReloadResult:
        """回滚到指定版本。"""
```

#### 24.4.3 测试编号

| 编号 | 描述 |
|------|------|
| T-HOTLOAD-01 | reload_skill 成功，新版本生效 |
| T-HOTLOAD-02 | reload_skill 失败，旧版本保持不变 |
| T-HOTLOAD-03 | rollback_skill 回滚到指定版本 |
| T-HOTLOAD-04 | 热加载写 skill_reload_log（审计） |
| T-HOTLOAD-05 | 运行时一致性检查：SkillMeta 字段缺失 → 拒绝加载 |

---

## §25 M13 工程契约（Bond 阶段自动晋级 + 长期记忆蒸馏强化）

### §25.1 M13-A Bond 阶段积分 + 自动晋级（FR-BOND-001/002/003）

#### 25.1.1 模块边界

```
host_brain/bond/
  bond_stage_engine.py    ← BondStageEngine（NEW）
host_brain/app/
  bond_routes.py          ← Bond REST API 5 端点（NEW）
```

#### 25.1.2 BondStageEngine 接口

```python
@dataclass(frozen=True)
class BondScoreState:
    stage: str                          # "stranger" | "acquaintance" | "friend" | "close_friend" | "soulmate"
    score: float                        # 内部积分（不暴露给语义内核）
    trend: Literal["rising", "stable", "falling"]
    last_event_ts: int
    audit_ref: str                      # 最近一次审计链引用

@dataclass(frozen=True)
class StageTransitionResult:
    previous_stage: str
    new_stage: str
    delta: float
    transition_type: Literal["upgrade", "downgrade", "stable"]
    audit_ref: str
    ts_ms: int

class BondStageEngine:
    def apply_event(
        self,
        event: dict,                    # etype / intensity / evidence_refs
        current_state: BondScoreState,
    ) -> tuple[BondScoreState, StageTransitionResult]:
        """应用 Bond 事件，返回新状态与晋级/降级结果；晋级/降级写入审计链。"""

    def get_summary(
        self,
        state: BondScoreState,
    ) -> dict:
        """返回给语义内核的 Bond Summary（仅 stage/trend/capability_hint，不含数值）。"""

    def get_trend(self, history: list[BondScoreState]) -> str:
        """计算趋势（rising/stable/falling）。"""
```

#### 25.1.3 Bond REST API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/bond/stage` | GET | 返回当前 Bond 阶段状态（不含数值） |
| `/bond/summary` | GET | 返回给语义内核的 Bond Summary |
| `/bond/event` | POST | 提交 Bond 事件（需 evidence_refs） |
| `/bond/users` | GET | 列出所有用户的 Bond 状态 |
| `/bond/health` | GET | 健康检查 |

#### 25.1.4 审计字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `stage_transition` | dict | 晋级/降级结果（previous/new/delta/type） |
| `audit_ref` | str | 审计链引用 |
| `evidence_refs` | list[str] | 触发事件的证据引用 |
| `ts_ms` | int | 时间戳 |

#### 25.1.5 错误码

| 错误码 | 触发条件 |
|--------|---------|
| `BOND_UPDATE_INVALID` | 缺 evidence_refs 的 Bond 更新 |
| `BOND_STAGE_UNKNOWN` | 未知 stage 值 |
| `BOND_EVENT_INVALID` | 事件格式不合规 |

#### 25.1.6 测试编号

| 编号 | 描述 |
|------|------|
| T-BOND-STAGE-01 | apply_event 积分累积，stage 不变 |
| T-BOND-STAGE-02 | 积分超阈值 → 自动晋级，写审计链 |
| T-BOND-STAGE-03 | 积分低于阈值 → 自动降级，写审计链 |
| T-BOND-STAGE-04 | get_summary 不含数值（FR-BOND-003） |
| T-BOND-STAGE-05 | 缺 evidence_refs → BOND_UPDATE_INVALID |
| T-BOND-STAGE-06 | get_trend 返回 rising/stable/falling |
| T-BOND-STAGE-07 | 边界：score=0 不降级到 stranger 以下 |
| T-BOND-STAGE-08 | 边界：score=max 不晋级到 soulmate 以上 |
| T-BOND-STAGE-09 | 审计链可追溯 stage_transition 字段 |
| T-BOND-API-01 | GET /bond/stage 返回结构稳定 |
| T-BOND-API-02 | GET /bond/summary 不含数值 |
| T-BOND-API-03 | POST /bond/event 成功，返回 stage_transition |
| T-BOND-API-04 | POST /bond/event 缺 evidence_refs → 422 |
| T-BOND-API-05 | GET /bond/users 返回列表结构稳定 |
| T-BOND-API-06 | GET /bond/health 返回 status/latency_ms |
| T-BOND-API-07 | Bond Summary 不含数值（FR-BOND-003 API 层验收） |
| T-BOND-API-08 | 非法 event 格式 → 422 + reason_code |

---

### §25.2 M13-B 长期记忆蒸馏强化（FR-MEM-DISTILL-001/002）

#### 25.2.1 模块边界

```
host_brain/memory/
  long_term_distiller.py  ← LongTermMemoryDistiller（NEW）
```

#### 25.2.2 LongTermMemoryDistiller 接口

```python
@dataclass(frozen=True)
class DistillationProposal:
    proposal_id: str
    source_episodic_ids: list[str]      # 来源 EpisodicMemory ID
    semantic_summary: str               # 语义摘要
    confidence: float
    evidence_refs: list[str]
    status: Literal["pending", "approved", "rejected"]
    ts_ms: int

class LongTermMemoryDistiller:
    def distill(
        self,
        episodic_memories: list[dict],
        *,
        max_proposals: int = 5,         # 每次最多 5 个提案
    ) -> list[DistillationProposal]:
        """将 EpisodicMemory 蒸馏为语义记忆提案（pending 状态，不直接写 Canon SSOT）。"""

    def is_duplicate(
        self,
        proposal: DistillationProposal,
        existing: list[DistillationProposal],
    ) -> bool:
        """幂等保护：检查是否已存在相同提案。"""

def make_distillation_task_config(
    *,
    cron: str = "0 3 * * *",           # 每日 03:00
    max_proposals: int = 5,
    enabled: bool = True,
) -> dict:
    """生成调度器 CRON 任务配置（工厂函数）。"""
```

#### 25.2.3 关键约束

- 蒸馏结果只生成 `pending` 提案，**不直接写 Canon SSOT**（FR-MEM-DISTILL-001 红线）
- 每次循环最多 5 个提案（防止批量污染）
- 幂等保护：同一 source_episodic_ids 组合不重复生成提案
- 提案必须携带 `evidence_refs`（来源 EpisodicMemory ID）

#### 25.2.4 测试编号

| 编号 | 描述 |
|------|------|
| T-DISTILL-B-01 | distill 返回 pending 提案列表，不写 Canon |
| T-DISTILL-B-02 | 提案 status=pending（不直接写 SSOT） |
| T-DISTILL-B-03 | 每次最多 5 个提案（max_proposals 边界） |
| T-DISTILL-B-04 | 幂等：相同 source_episodic_ids 不重复生成 |
| T-DISTILL-B-05 | 提案含 evidence_refs（来源 EpisodicMemory ID） |
| T-DISTILL-B-06 | episodic_memories 为空 → 返回空列表 |
| T-DISTILL-B-07 | 语义摘要不伪造事实（confidence 字段可观测） |
| T-DISTILL-B-08 | is_duplicate 正确识别重复提案 |
| T-DISTILL-B-09 | make_distillation_task_config 返回合法 CRON 配置 |
| T-DISTILL-B-10 | make_distillation_task_config 非法参数 → 抛出 ValueError |

---

### §25.3 M13-C Bond 事件端到端集成（FR-BOND-001/002）

#### 25.3.1 模块边界

```
host_brain/bond/
  bond_dynamics.py        ← BondDynamicsWriter（已有，需强化）
```

#### 25.3.2 BondDynamicsWriter 强化接口

```python
class BondDynamicsWriter:
    def __init__(
        self,
        *,
        stage_engine: BondStageEngine | None = None,  # 可选注入，向后兼容
    ): ...

    def apply_update(
        self,
        event: dict,
        *,
        user_id: str,
    ) -> dict:
        """
        应用 Bond 更新：
        1. 写 Neo4j SSOT（原有逻辑）
        2. 若 stage_engine 已注入，调用 apply_event 并将 stage_transition 写入 event 结果
        3. stage_engine 异常时非致命（降级，记录 warn，不中断主流程）
        """
```

#### 25.3.3 event 结果新增字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `stage_transition` | dict \| None | 晋级/降级结果；stage_engine=None 时为 None |
| `stage_engine_error` | str \| None | stage_engine 异常时的错误摘要（非致命） |

#### 25.3.4 测试编号

| 编号 | 描述 |
|------|------|
| T-BOND-INTEG-01 | apply_update 成功，event 含 stage_transition |
| T-BOND-INTEG-02 | stage_engine=None → stage_transition=None（向后兼容） |
| T-BOND-INTEG-03 | stage_engine 异常 → 非致命，主流程继续 |
| T-BOND-INTEG-04 | stage_transition 写入审计链 |
| T-BOND-INTEG-05 | 缺 evidence_refs → BOND_UPDATE_INVALID（FR-BOND-002） |
| T-BOND-INTEG-06 | 端到端：Bond 事件 → 晋级 → 审计链可追溯 |
| T-BOND-INTEG-07 | 端到端：Bond 事件 → 降级 → 审计链可追溯 |
| T-BOND-INTEG-08 | 旧调用路径（无 stage_engine）零影响 |

---

## §26 M13 发布清单工程契约

### §26.1 发布清单 Schema

```json
{
  "milestone": "M13",
  "release_date": "2026-03-04",
  "release_ready": true,
  "milestones": {
    "M13-A": "DONE",
    "M13-B": "DONE",
    "M13-C": "DONE",
    "M13-D": "DONE"
  },
  "core_files": {
    "host_brain/bond/bond_stage_engine.py": "<sha256>",
    "host_brain/app/bond_routes.py": "<sha256>",
    "host_brain/memory/long_term_distiller.py": "<sha256>",
    "host_brain/bond/bond_dynamics.py": "<sha256>"
  },
  "test_summary": {
    "m13_tests_passed": 102,
    "total_tests_passed": 1342,
    "skipped": 2,
    "failed": 0
  },
  "quality_gates": {
    "all_core_files_present": true,
    "zero_regressions": true,
    "audit_chain_verified": true
  }
}
```

### §26.2 质量门禁

| 门禁项 | 要求 |
|--------|------|
| `release_ready` | 必须为 true |
| `all_core_files_present` | 所有核心文件 hash 已填写 |
| `zero_regressions` | failed=0 |
| `audit_chain_verified` | 审计链字段可追溯 |

---

## §27 M14 工程契约（通道 + 外显 + MCP 治理收口）

### §27.1 M14-A 微信通道 I/O 事件化（FR-CHANNEL-001）

#### 27.1.1 模块边界

```
host_brain/channels/
  wechat_channel.py       ← WeChatChannelEvent（NEW）
host_brain/core/
  errors.py               ← 新增 CHANNEL_* 错误码
tests/unit/
  test_wechat_channel.py  ← T-CHAN-01~05
tests/integration/
  test_wechat_channel_audit.py  ← T-CHAN-AUDIT-01~03
```

#### 27.1.2 WeChatChannelEvent Schema

```python
from enum import Enum
from dataclasses import dataclass

class ChannelEventType(str, Enum):
    INBOUND  = "inbound"    # 用户→Host
    OUTBOUND = "outbound"   # Host→用户

@dataclass(frozen=True)
class WeChatChannelEvent:
    event_id: str                           # UUID
    event_type: ChannelEventType
    thread_key: str                         # 会话线程标识（可追溯）
    request_id: str                         # 关联 Host 请求 ID
    intent_id: str | None                   # 关联 intent（outbound 必填）
    payload: dict                           # 消息内容（净化后）
    audit_ref: str                          # 审计链引用
    ts_ms: int
    reason_code: str | None = None          # 失败时必填
    stage: str | None = None                # 失败阶段
```

#### 27.1.3 通道事件处理接口

```python
class WeChatChannelHandler:
    def receive(
        self,
        raw_payload: dict,
        *,
        thread_key: str,
        request_id: str,
    ) -> WeChatChannelEvent:
        """
        接收微信 inbound 消息，建模为 WeChatChannelEvent。
        - 写入审计链（channel_event_log）
        - 返回结构化事件（不含原始敏感字段）
        """

    def send(
        self,
        message: dict,
        *,
        thread_key: str,
        request_id: str,
        intent_id: str,
        approval_token: str,
    ) -> WeChatChannelEvent:
        """
        发送微信 outbound 消息。
        - LLM 不得自行决定"是否发送/发给谁/发什么"
        - 必须有 approval_token（WRITE 操作）
        - 写入审计链（channel_event_log）
        - 失败必须返回 reason_code + stage
        """
```

#### 27.1.4 审计字段（channel_event_log）

| 字段 | 类型 | 说明 |
|------|------|------|
| `event_id` | str | 通道事件唯一 ID |
| `event_type` | str | inbound / outbound |
| `thread_key` | str | 会话线程标识 |
| `request_id` | str | 关联 Host 请求 ID |
| `intent_id` | str \| None | 关联 intent（outbound 必填） |
| `audit_ref` | str | 审计链引用 |
| `reason_code` | str \| None | 失败时必填 |
| `stage` | str \| None | 失败阶段 |
| `ts_ms` | int | 时间戳 |

#### 27.1.5 错误码

| 错误码 | 触发条件 |
|--------|---------|
| `CHANNEL_SEND_UNAUTHORIZED` | outbound 无 approval_token |
| `CHANNEL_INTENT_MISSING` | outbound 无 intent_id |
| `CHANNEL_PAYLOAD_INVALID` | 消息内容格式不合规 |
| `CHANNEL_THREAD_KEY_MISSING` | 缺 thread_key |
| `CHANNEL_AUDIT_WRITE_FAILED` | 审计链写入失败（非致命，降级） |

#### 27.1.6 测试编号

| 编号 | 描述 |
|------|------|
| T-CHAN-01 | receive 成功，返回 WeChatChannelEvent（inbound），写审计链 |
| T-CHAN-02 | send 成功，返回 WeChatChannelEvent（outbound），写审计链 |
| T-CHAN-03 | send 无 approval_token → CHANNEL_SEND_UNAUTHORIZED |
| T-CHAN-04 | send 无 intent_id → CHANNEL_INTENT_MISSING |
| T-CHAN-05 | 缺 thread_key → CHANNEL_THREAD_KEY_MISSING |
| T-CHAN-AUDIT-01 | outbound 事件可在审计链追溯到 thread_key + request_id |
| T-CHAN-AUDIT-02 | inbound 事件可在审计链追溯到 thread_key + request_id |
| T-CHAN-AUDIT-03 | 失败事件含 reason_code + stage，可回放到 intent_id |

---

### §27.2 M14-B VTS 外显确定性映射（FR-AVATAR-001）

#### 27.2.1 模块边界

```
edge_client/actuators/
  vts_expression_map.py   ← VTSExpressionMap（NEW）
  vts_driver.py           ← 强化：接入 VTSExpressionMap（已有）
host_brain/core/
  errors.py               ← 新增 VTS_* 错误码
tests/unit/
  test_vts_expression_map.py  ← T-VTS-01~05
```

#### 27.2.2 VTSExpressionMap Schema

```python
from enum import Enum
from dataclasses import dataclass

class EmotionTag(str, Enum):
    """固定枚举 emotion_tag（不允许自由扩展）"""
    NEUTRAL   = "neutral"
    HAPPY     = "happy"
    SAD       = "sad"
    ANGRY     = "angry"
    SURPRISED = "surprised"
    FEARFUL   = "fearful"
    DISGUSTED = "disgusted"
    EXCITED   = "excited"
    CALM      = "calm"

# 固定映射表（同版本同输入输出一致）
EXPRESSION_MAP: dict[EmotionTag, str] = {
    EmotionTag.NEUTRAL:   "exp_neutral_01",
    EmotionTag.HAPPY:     "exp_happy_01",
    EmotionTag.SAD:       "exp_sad_01",
    EmotionTag.ANGRY:     "exp_angry_01",
    EmotionTag.SURPRISED: "exp_surprised_01",
    EmotionTag.FEARFUL:   "exp_fearful_01",
    EmotionTag.DISGUSTED: "exp_disgusted_01",
    EmotionTag.EXCITED:   "exp_excited_01",
    EmotionTag.CALM:      "exp_calm_01",
}

@dataclass(frozen=True)
class ExpressionTrigger:
    emotion_tag: EmotionTag
    expression_id: str                  # 来自 EXPRESSION_MAP
    intensity: float                    # 0.0~1.0
    duration_ms: int
    request_id: str
    ts_ms: int

class VTSExpressionMap:
    def resolve(
        self,
        emotion_tag: str,
        *,
        intensity: float = 1.0,
        duration_ms: int = 2000,
        request_id: str,
    ) -> ExpressionTrigger:
        """
        将 emotion_tag 映射为 ExpressionTrigger。
        - 非法 tag → VTS_TAG_UNKNOWN
        - 自由动作参数（非枚举值）→ VTS_EXPRESSION_INVALID
        - 同版本同输入输出一致（可回放）
        """

    def list_tags(self) -> list[str]:
        """返回所有合法 emotion_tag 枚举值。"""
```

#### 27.2.3 VTSDriver 强化要点

- 接入 `VTSExpressionMap.resolve()`，不再接受原始字符串 tag
- 非法 tag/自由动作参数在 `resolve()` 阶段拒绝，不进入 VTS 调用
- 触发记录写入 `vts_trigger_log`（含 expression_id/intensity/request_id）

#### 27.2.4 错误码

| 错误码 | 触发条件 |
|--------|---------|
| `VTS_TAG_UNKNOWN` | emotion_tag 不在枚举中 |
| `VTS_EXPRESSION_INVALID` | 自由动作参数或非法 expression_id |
| `VTS_INTENSITY_OUT_OF_RANGE` | intensity 不在 0.0~1.0 |
| `VTS_DURATION_INVALID` | duration_ms ≤ 0 |

#### 27.2.5 测试编号

| 编号 | 描述 |
|------|------|
| T-VTS-01 | resolve 合法 tag → 返回 ExpressionTrigger，expression_id 固定 |
| T-VTS-02 | 非法 emotion_tag → VTS_TAG_UNKNOWN |
| T-VTS-03 | 自由动作参数（非枚举值）→ VTS_EXPRESSION_INVALID |
| T-VTS-04 | 同版本同输入输出一致（可回放映射表） |
| T-VTS-05 | list_tags 返回所有合法枚举值 |

---

### §27.3 M14-C MCP Provider 治理完整验收（FR-MCP-001）

#### 27.3.1 模块边界

```
host_brain/tools/
  mcp_adapter.py          ← 强化：审计串联 + AIRLOCK 出站最小化（已有）
tests/unit/
  test_mcp_governance.py  ← T-MCP-GOV-01~05
tests/integration/
  test_mcp_audit_chain.py ← T-MCP-AUDIT-01~03
```

#### 27.3.2 MCP 调用审计字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `provider_id` | str | MCP provider 唯一标识 |
| `tool_id` | str | 工具唯一标识 |
| `request_id` | str | 关联 Host 请求 ID |
| `intent_id` | str | 关联 intent |
| `evidence_ref` | str | 执行证据引用 |
| `access_level` | str | READ / WRITE |
| `approval_token_ref` | str \| None | WRITE 必填 |
| `airlock_sanitized` | bool | 是否经过 AIRLOCK 净化 |
| `ts_ms` | int | 时间戳 |

#### 27.3.3 AIRLOCK 出站最小化要求

- 出站 payload 必须经 AIRLOCK 净化（移除硬状态原始值/宪法全文/规则细节）
- 每次出站生成 `sanitization_report`（removed_fields/redactions/payload_digest）
- 缺失 `sanitization_report` → 拒绝出站（`AIRLOCK_BLOCKED_EGRESS`）

#### 27.3.4 MCP ToolMeta 同步接口

```python
class MCPAdapter:
    def sync_provider_tools(
        self,
        provider_id: str,
        *,
        provider_url: str,
    ) -> list[ToolMeta]:
        """
        同步 MCP provider 暴露的工具为 ToolMeta 列表。
        - 仅同步元数据，不执行
        - 按 READ/WRITE 分级
        - 写入 tool_sync_log（审计）
        """

    def execute_mcp_tool(
        self,
        tool_id: str,
        args: dict,
        *,
        request_id: str,
        intent_id: str,
        approval_token: str | None = None,
    ) -> dict:
        """
        执行 MCP 工具（必须经 ToolBroker 单入口）。
        - WRITE 必须有 approval_token
        - 出站经 AIRLOCK 净化
        - 写入 mcp_call_log（审计）
        """
```

#### 27.3.5 错误码

| 错误码 | 触发条件 |
|--------|---------|
| `MCP_WRITE_UNAUTHORIZED` | WRITE 工具无 approval_token 或未 APPROVE |
| `MCP_PROVIDER_SYNC_FAILED` | provider 元数据同步失败 |
| `MCP_TOOL_NOT_FOUND` | tool_id 不在已同步列表 |
| `MCP_AUDIT_REF_MISSING` | 执行无 evidence_ref |
| `AIRLOCK_BLOCKED_EGRESS` | 出站缺 sanitization_report |

#### 27.3.6 测试编号

| 编号 | 描述 |
|------|------|
| T-MCP-GOV-01 | sync_provider_tools 成功，返回 ToolMeta 列表（含 READ/WRITE 分级） |
| T-MCP-GOV-02 | WRITE 工具无 approval_token → MCP_WRITE_UNAUTHORIZED |
| T-MCP-GOV-03 | WRITE 工具未 APPROVE → MCP_WRITE_UNAUTHORIZED |
| T-MCP-GOV-04 | 出站缺 sanitization_report → AIRLOCK_BLOCKED_EGRESS |
| T-MCP-GOV-05 | 执行无 evidence_ref → MCP_AUDIT_REF_MISSING |
| T-MCP-AUDIT-01 | MCP 调用可追溯到 intent/governor/skill_exec 记录 |
| T-MCP-AUDIT-02 | MCP WRITE 调用含 approval_token_ref + evidence_ref |
| T-MCP-AUDIT-03 | AIRLOCK 净化报告可回放（removed_fields/payload_digest） |

---

### §27.4 M14-D 发布清单工程契约

#### 27.4.1 发布清单 Schema

```json
{
  "milestone": "M14",
  "release_date": "TBD",
  "release_ready": false,
  "milestones": {
    "M14-A": "PLANNED",
    "M14-B": "PLANNED",
    "M14-C": "PLANNED",
    "M14-D": "PLANNED"
  },
  "core_files": {
    "host_brain/channels/wechat_channel.py": "",
    "edge_client/actuators/vts_expression_map.py": "",
    "edge_client/actuators/vts_driver.py": "",
    "host_brain/tools/mcp_adapter.py": ""
  },
  "test_summary": {
    "m14_tests_passed": 0,
    "total_tests_passed": 0,
    "skipped": 0,
    "failed": 0
  },
  "quality_gates": {
    "all_core_files_present": false,
    "zero_regressions": false,
    "audit_chain_verified": false
  }
}
```

#### 27.4.2 质量门禁（M14 验收标准）

| 门禁项 | 要求 |
|--------|------|
| `release_ready` | 必须为 true |
| `all_core_files_present` | 4 个核心文件 hash 已填写 |
| `zero_regressions` | failed=0 |
| `total_tests_passed` | ≥ 1370 |
| `audit_chain_verified` | 通道/外显/MCP 审计链字段可追溯 |

---

## §28 M15 工程契约（语音链路深化完整验收）

### §28.1 M15-A ASR 事件化上送强化（FR-VOICE-001）

#### 28.1.1 模块边界

```
edge_client/senses/
  asr.py                  ← ASREventEmitter 强化（已有，需补 alternates + evidence_ref）
tests/unit/
  test_asr_event_emitter.py  ← T-ASR-EVT-01~05（NEW）
```

#### 28.1.2 ASRTranscriptEvent Schema（强化）

```python
@dataclass(frozen=True)
class ASRTranscriptEvent:
    event_id: str
    session_id: str
    segment_id: str
    text: str
    confidence: float               # 0.0~1.0
    ts_start_ms: int
    ts_end_ms: int
    is_final: bool
    alternates: list[str]           # N-best（可选，最多 3 个）
    evidence_ref: str               # 审计链引用（必填）
    low_confidence: bool            # confidence < 0.6 时为 True
    reason_code: str | None         # 低置信度时填 ASR_LOW_CONFIDENCE
```

#### 28.1.3 关键约束

- 低置信度（confidence < 0.6）必须显式标注 `low_confidence=True` + `reason_code=ASR_LOW_CONFIDENCE`
- 每个事件必须携带 `evidence_ref`（可追溯）
- `alternates` 最多 3 个（防止过度暴露）
- 流式/分段均适用同一 Schema

#### 28.1.4 测试编号

| 编号 | 描述 |
|------|------|
| T-ASR-EVT-01 | 正常转写事件，confidence ≥ 0.6，low_confidence=False |
| T-ASR-EVT-02 | 低置信度，confidence < 0.6，low_confidence=True，reason_code=ASR_LOW_CONFIDENCE |
| T-ASR-EVT-03 | alternates 最多 3 个（边界：4 个时截断） |
| T-ASR-EVT-04 | evidence_ref 必填（缺失时拒绝构建） |
| T-ASR-EVT-05 | is_final=True 时事件可回放（含 ts_start_ms/ts_end_ms） |

---

### §28.2 M15-B TTS 播报队列强化（FR-VOICE-002）

#### 28.2.1 模块边界

```
edge_client/actuators/
  tts_engine.py           ← TTSPlaybackQueue 强化（已有，需补 request_id 关联 + 失败 reason_code）
tests/unit/
  test_tts_playback_queue.py  ← T-TTS-QUEUE-01~05（NEW）
```

#### 28.2.2 TTSPlaybackEvent Schema（强化）

```python
@dataclass(frozen=True)
class TTSPlaybackEvent:
    event_id: str
    request_id: str                 # 关联 Host 请求 ID（必填）
    intent_id: str | None           # 关联 intent（可选）
    text: str                       # 最终口径文本（Host QA 后）
    emotion_tag: str                # 情绪标签（来自 EmotionTag 枚举）
    status: str                     # "queued" | "playing" | "done" | "interrupted" | "failed"
    reason_code: str | None         # 失败时必填
    stage: str | None               # 失败阶段
    ts_ms: int
    evidence_ref: str               # 审计链引用
```

#### 28.2.3 关键约束

- Host 只下发最终口径文本与情绪标签（不含执行事实宣告）
- 播报事件必须关联 `request_id`（可追溯）
- 失败必须返回 `reason_code` + `stage`（TTS_QUEUE_FULL / TTS_SYNTH_FAILED / TTS_PLAYBACK_FAILED）
- 不得产生伪执行宣告

#### 28.2.4 测试编号

| 编号 | 描述 |
|------|------|
| T-TTS-QUEUE-01 | 正常入队，status=queued，evidence_ref 存在 |
| T-TTS-QUEUE-02 | 播报完成，status=done，request_id 可追溯 |
| T-TTS-QUEUE-03 | 队列满，status=failed，reason_code=TTS_QUEUE_FULL |
| T-TTS-QUEUE-04 | 合成失败，status=failed，reason_code=TTS_SYNTH_FAILED |
| T-TTS-QUEUE-05 | 缺 request_id → 拒绝入队（VOICE_SCHEMA_INVALID） |

---

### §28.3 M15-C Barge-in 可打断强化（FR-VOICE-003）

#### 28.3.1 模块边界

```
edge_client/actuators/
  tts_engine.py           ← interrupt() 方法强化（已有，需补审计记录）
tests/unit/
  test_barge_in.py        ← T-BARGE-01~04（NEW）
```

#### 28.3.2 BargeInEvent Schema

```python
@dataclass(frozen=True)
class BargeInEvent:
    event_id: str
    session_id: str
    interrupted_request_id: str     # 被打断的播报 request_id
    trigger_ts_ms: int              # 打断触发时间
    asr_continued: bool             # ASR 是否继续上送
    evidence_ref: str               # 审计链引用
```

#### 28.3.3 关键约束

- 打断立即生效（不等待当前句子播完）
- 打断不构成任何执行事实或写入事实
- ASR 不被阻塞（打断后 ASR 继续上送）
- 打断事件写入审计链（barge_in_log）

#### 28.3.4 测试编号

| 编号 | 描述 |
|------|------|
| T-BARGE-01 | 打断立即生效，interrupted_request_id 可追溯 |
| T-BARGE-02 | 打断后 ASR 继续上送（asr_continued=True） |
| T-BARGE-03 | 打断不产生执行事实（无 intent_draft） |
| T-BARGE-04 | 打断事件写入审计链（evidence_ref 存在） |

---

### §28.4 M15-D Edge 兜底小模型门控（FR-VOICE-004）

#### 28.4.1 模块边界

```
edge_client/
  fallback_model.py       ← FallbackModelGate（NEW）
tests/unit/
  test_fallback_model_gate.py  ← T-FALLBACK-01~05（NEW）
```

#### 28.4.2 FallbackModelGate 接口

```python
@dataclass(frozen=True)
class FallbackOutput:
    output_id: str
    text: str
    draft_only: bool = True         # 永远为 True（不得包含裁决或执行事实）
    reason_code: str | None = None  # 不可用时填 FALLBACK_MODEL_UNAVAILABLE
    stage: str | None = None
    evidence_ref: str | None = None

class FallbackModelGate:
    def generate(
        self,
        prompt: str,
        *,
        task_type: str,             # "scene_response" | "input_clean" | "summary" | "retrieval_hint"
        request_id: str,
        ts_ms: int | None = None,
    ) -> FallbackOutput:
        """
        调用兜底小模型（Ollama 3B 量化）。
        - 输出永远标记为 draft_only=True
        - 不可用时快速失败（FALLBACK_MODEL_UNAVAILABLE）
        - 不得包含裁决或执行事实宣告
        """

    def is_available(self) -> bool:
        """检查兜底模型是否可用（已加载）。"""

    def load(self) -> bool:
        """按需加载兜底模型（默认卸载）。"""

    def unload(self) -> None:
        """卸载兜底模型（释放资源）。"""
```

#### 28.4.3 关键约束

- 默认卸载，按需加载（不常驻 GPU）
- 输出永远标记为 `draft_only=True`
- 不可用时快速失败（不阻塞主流程）
- 不得包含裁决或执行事实宣告

#### 28.4.4 测试编号

| 编号 | 描述 |
|------|------|
| T-FALLBACK-01 | generate 成功，draft_only=True |
| T-FALLBACK-02 | 不可用时快速失败，reason_code=FALLBACK_MODEL_UNAVAILABLE |
| T-FALLBACK-03 | 输出不含裁决/执行事实宣告（QA 断言） |
| T-FALLBACK-04 | load/unload 生命周期正确 |
| T-FALLBACK-05 | task_type 非法 → VOICE_SCHEMA_INVALID |

---

### §28.5 M15-E Edge 资源闸门强化（FR-VOICE-005）

#### 28.5.1 模块边界

```
edge_client/
  resource_gate.py        ← EdgeResourceGate 强化（已有，需补优先级抢占审计）
tests/unit/
  test_edge_resource_gate.py  ← T-RGATE-01~05（NEW）
```

#### 28.5.2 ResourcePreemptionEvent Schema

```python
@dataclass(frozen=True)
class ResourcePreemptionEvent:
    event_id: str
    preemptor: str                  # 抢占者（"ASR" | "PLAYBACK" | "TTS" | "FALLBACK_LLM"）
    preempted: str                  # 被抢占者
    reason_code: str                # RESOURCE_GATE_PREEMPTED
    ts_ms: int
    evidence_ref: str
```

#### 28.5.3 优先级规则（固定）

```
ASR > PLAYBACK > TTS_SYNTH > FALLBACK_LLM
```

#### 28.5.4 关键约束

- 压力下仍保证 ASR（最高优先级）
- 资源不足时降级行为可观测（ResourcePreemptionEvent 写入审计链）
- 抢占事件含 preemptor/preempted/reason_code

#### 28.5.5 测试编号

| 编号 | 描述 |
|------|------|
| T-RGATE-01 | ASR 抢占 TTS_SYNTH，ResourcePreemptionEvent 写入审计链 |
| T-RGATE-02 | ASR 抢占 FALLBACK_LLM，reason_code=RESOURCE_GATE_PREEMPTED |
| T-RGATE-03 | PLAYBACK 抢占 TTS_SYNTH（ASR 不在运行时） |
| T-RGATE-04 | 压力下 ASR 始终保证（不被任何任务抢占） |
| T-RGATE-05 | 抢占事件含 preemptor/preempted/evidence_ref |

---

### §28.6 M15 发布清单工程契约

#### 28.6.1 发布清单 Schema

```json
{
  "milestone": "M15",
  "release_date": "TBD",
  "release_ready": false,
  "milestones": {
    "M15-A": "PLANNED",
    "M15-B": "PLANNED",
    "M15-C": "PLANNED",
    "M15-D": "PLANNED",
    "M15-E": "PLANNED",
    "M15-F": "PLANNED"
  },
  "core_files": {
    "edge_client/senses/asr.py": "",
    "edge_client/actuators/tts_engine.py": "",
    "edge_client/fallback_model.py": "",
    "edge_client/resource_gate.py": ""
  },
  "test_summary": {
    "m15_tests_passed": 0,
    "total_tests_passed": 0,
    "skipped": 0,
    "failed": 0
  },
  "quality_gates": {
    "all_core_files_present": false,
    "zero_regressions": false,
    "audit_chain_verified": false
  }
}
```

#### 28.6.2 质量门禁（M15 验收标准）

| 门禁项 | 要求 |
|--------|------|
| `release_ready` | 必须为 true |
| `all_core_files_present` | 4 个核心文件 hash 已填写 |
| `zero_regressions` | failed=0 |
| `total_tests_passed` | ≥ 1445 |
| `audit_chain_verified` | ASR/TTS/Barge-in/兜底/资源闸门审计链字段可追溯 |

---

## 29. M17/M18 监控面板 v3 工程契约（前端 SPA + 后端 API 扩展）

> 对应 PRD §4.20（FR-OBS-003~011）。
> 当前状态：DONE（2026-03-04：React+Vite SPA 四页面全量可用；26 个新增后端测试全部通过；1577 全量通过，2 skipped，0 failed）。

---

### 29.1 总体定位与约束

**目标**：将现有 `panel.html` 单页重建为专业运维监控 SPA，参考 Dify 简约风格，实现双端全服务监控 + 节点链路可视化展示。

**覆盖需求**：FR-OBS-003~011。
**回归口径**：T-OBS-V3-ARCH-01~03、T-OBS-V3-OVW-01~05、T-OBS-V3-SESS-01~05、T-OBS-V3-TRACE-01~08、T-OBS-V3-METRICS-01~05、T-OBS-HEALTH-01~06、T-OBS-SESS-01~06、T-OBS-TRACE-01~08、T-OBS-TS-01~06。

**硬约束**：
- 前端产出物（`host_brain/app/static/monitor/`）由 FastAPI StaticFiles 托管，无需 Node.js 运行时。
- 生产构建：`npm run build` 产出物提交到仓库；开发模式：`npm run dev`（Vite dev server 代理 `/obs/*` 到 FastAPI）。
- 设计风格：Dify 简约白色风格（背景 `#F9FAFB`，主色 `#2970FF`，卡片圆角 `8px`，字体 Inter）；**严格禁用大面积高饱和度渐变与廉价发光阴影**；**禁止使用 Emoji 作为图标**。

---

### 29.2 技术栈约定（M17 强制执行）

| 技术 | 版本 | 用途 |
|------|------|------|
| React | 18.x | 组件化开发，所有页面均为 React 组件 |
| Vite | 5.x | 快速构建；`npm run build` 产出静态文件由 FastAPI StaticFiles 托管 |
| Tailwind CSS | 3.x | 原子化样式；实现 Dify 简约白色风格 |
| React Router | 6.x | 4 个核心页面的 History/Hash 路由切换 |
| React Flow | latest | Trace 节点流图（Dify Workflow 同款底层库，原生支持节点卡片、有向连线、分支/并行布局） |
| Apache ECharts | 5.x | Metrics 时序折线图、柱状图、条形图 |
| Zustand | 4.x | 全局状态管理（30s 轮询状态 + 全局时间范围过滤） |
| Lucide React | latest | 统一专业的纯色 SVG 图标 |

**项目结构约定（语义固定）**：
- 源码目录：`host_brain/app/monitor_ui/`（React 项目根，含 `package.json` / `vite.config.js`）
- 构建产出：`host_brain/app/static/monitor/`（FastAPI StaticFiles 托管，入口为 `index.html`）

---

### 29.3 前端架构契约（M17-A：§OBS-V3-001）

覆盖需求：FR-OBS-003。
回归口径：T-OBS-V3-ARCH-01~03。

#### 29.3.1 核心文件落点（语义固定）

```
host_brain/app/monitor_ui/
  package.json                          ← 依赖声明（react/vite/tailwind/react-router/reactflow/echarts/zustand/lucide-react）
  vite.config.js                        ← 构建配置（产出到 ../static/monitor/）
  tailwind.config.js                    ← Dify 设计 token
  src/
    main.jsx                            ← React 入口
    App.jsx                             ← 根组件 + React Router 路由配置
    store/index.js                      ← Zustand store（轮询状态 + 时间范围过滤）
    components/
      Layout.jsx                        ← 左侧固定导航栏 + 顶部面包屑 + 主内容区布局框架
      trace/
        CustomNode.jsx                  ← 自定义节点卡片组件（React Flow）
        NodeDetailPanel.jsx             ← 节点详情侧边面板
      charts/
        LineChart.jsx                   ← ECharts 折线图封装（可复用）
        BarChart.jsx                    ← ECharts 柱状/条形图封装（可复用）
    pages/
      Overview.jsx                      ← 全服务健康总览页
      Sessions.jsx                      ← 会话列表页
      Trace.jsx                         ← Dify 风格链路可视化页（React Flow）
      Metrics.jsx                       ← 指标大盘页（ECharts）
    utils/
      traceLayout.js                    ← Dagre 自动布局工具函数
host_brain/app/static/monitor/          ← npm run build 产出（提交到仓库）
```

#### 29.3.2 路由契约（4 个核心页面）

| 路由路径 | 页面组件 | 功能 |
|---------|---------|------|
| `/` 或 `/overview` | `Overview.jsx` | 全服务健康总览 |
| `/sessions` | `Sessions.jsx` | 会话列表 + 过滤 + 分页 |
| `/trace` | `Trace.jsx` | 链路节点可视化（React Flow） |
| `/metrics` | `Metrics.jsx` | 指标大盘（ECharts） |

#### 29.3.3 Zustand Store 契约（最小字段）

```javascript
{
  // 轮询状态
  pollingEnabled: boolean,          // 是否开启 30s 自动轮询
  lastPolledAt: number | null,      // 上次轮询时间戳（ms）
  pollingError: string | null,      // 轮询错误信息

  // 全局时间范围过滤
  timeRange: '1h' | '6h' | '24h' | '7d',

  // 当前选中的 request_id（Sessions → Trace 跳转传递）
  selectedRequestId: string | null,
}
```

#### 29.3.4 验收测试编号

| 编号 | 描述 |
|------|------|
| T-OBS-V3-ARCH-01 | `npm run dev` 启动无报错，四个页面均可通过路由切换 |
| T-OBS-V3-ARCH-02 | `npm run build` 成功产出，FastAPI 可正常托管 |
| T-OBS-V3-ARCH-03 | 左侧导航高亮当前页；1280×800 分辨率下布局正常，无横向滚动条 |

---

### 29.4 全服务健康总览页契约（M17-B：§OBS-V3-002）

覆盖需求：FR-OBS-004。
回归口径：T-OBS-V3-OVW-01~05。

#### 29.4.1 页面功能契约

- 顶部汇总统计行：总服务数 / 健康数 / 降级数 / 故障数（与卡片数量严格一致）。
- 服务卡片网格：每卡片含服务名、侧（Host/Edge）、状态徽章（UP/DOWN/DEGRADED）、响应延迟、最后检查时间、错误摘要（DOWN 时展示）。
- 30 秒自动轮询 `GET /obs/services/health`；轮询失败时显示"上次已知状态 HH:MM:SS"，不崩溃。
- DOWN 状态卡片显示红色边框 + 错误摘要；DEGRADED 显示橙色边框。

#### 29.4.2 验收测试编号

| 编号 | 描述 |
|------|------|
| T-OBS-V3-OVW-01 | 所有已注册服务（≥12 个）均出现在总览页 |
| T-OBS-V3-OVW-02 | DOWN 状态卡片显示红色边框 + 错误摘要 |
| T-OBS-V3-OVW-03 | 轮询失败时显示"上次已知状态 HH:MM:SS"，不崩溃 |
| T-OBS-V3-OVW-04 | 顶部汇总数字与卡片数量一致 |
| T-OBS-V3-OVW-05 | `?side=host|edge` 过滤参数正确生效 |

---

### 29.5 会话列表页契约（M17-C：§OBS-V3-003）

覆盖需求：FR-OBS-005。
回归口径：T-OBS-V3-SESS-01~05。

#### 29.5.1 页面功能契约

- 顶部过滤栏：时间范围选择器 + 状态下拉（OK/REJECT/ERROR）+ 关键词搜索。
- 表格列：request_id / 时间 / 状态 / 耗时 / 通道 / reason_code。
- 底部分页控件（每页 20 条），翻页不丢失过滤条件。
- 点击行跳转到 Trace 页并传递 request_id（URL hash 变为 `#trace?rid=xxx`）。
- 空结果时显示友好空状态（非报错）。

#### 29.5.2 验收测试编号

| 编号 | 描述 |
|------|------|
| T-OBS-V3-SESS-01 | 列表正常分页（每页 20 条），翻页不丢失过滤条件 |
| T-OBS-V3-SESS-02 | 点击行后 URL hash 变为 `#trace?rid=xxx`，Trace 页自动加载 |
| T-OBS-V3-SESS-03 | 状态过滤（OK/REJECT/ERROR）正确生效 |
| T-OBS-V3-SESS-04 | 空结果时显示友好空状态（非报错） |
| T-OBS-V3-SESS-05 | total_count 与实际数据一致 |

---

### 29.6 链路可视化页契约（M17-D：§OBS-V3-004）

覆盖需求：FR-OBS-006。
回归口径：T-OBS-V3-TRACE-01~08。

#### 29.6.1 页面功能契约

- React Flow 画布：自动布局（从左到右，Dagre 布局算法），支持分支/并行，支持缩放/平移。
- 自定义节点卡片（CustomNode）：圆角矩形，含 Lucide React 节点类型图标、节点名称、状态颜色（绿/红/橙/蓝动画）、耗时标签、Provider 徽章（LLM 节点专属）。
- 有向连线：React Flow 内置 Edge，支持分支合并，箭头样式。
- 节点点击侧边面板（NodeDetailPanel）：展示 input/output（JSON 格式化 + 折叠/展开）、status、reason_code、时间戳、side（Host/Edge）、model_id（LLM 节点）。
- 顶部工具栏：request_id 输入框 + 查询按钮 + 全屏切换。
- 对接 `GET /obs/trace/{request_id}` API。

#### 29.6.2 验收测试编号

| 编号 | 描述 |
|------|------|
| T-OBS-V3-TRACE-01 | 给定 request_id 后节点图正确渲染（≤50 节点流畅，无卡顿） |
| T-OBS-V3-TRACE-02 | 点击节点侧边面板正确显示 input/output |
| T-OBS-V3-TRACE-03 | 并行节点以分支形式展示（不重叠，Dagre 布局正确） |
| T-OBS-V3-TRACE-04 | JSON 折叠/展开功能正常 |
| T-OBS-V3-TRACE-05 | 全屏模式可用 |
| T-OBS-V3-TRACE-06 | 空链路时显示友好空状态 |
| T-OBS-V3-TRACE-07 | LLM 节点卡片显示 Provider 徽章（OpenAI/Gemini/DeepSeek 颜色区分） |
| T-OBS-V3-TRACE-08 | input/output 超 64KB 时 truncated=true 标注存在 |

---

### 29.7 指标大盘页契约（M17-E：§OBS-V3-005）

覆盖需求：FR-OBS-007。
回归口径：T-OBS-V3-METRICS-01~05。

#### 29.7.1 页面功能契约

- 时间范围选择器（1h / 6h / 24h / 7d，Zustand 状态驱动）。
- 请求量趋势折线图（ECharts Canvas）。
- 拒绝率趋势折线图（ECharts Canvas）。
- reason_code 分布横向条形图（ECharts Canvas）。
- 各服务响应延迟 P50/P95 柱状图（ECharts Canvas）。
- 对接 `GET /obs/metrics/timeseries` API。

#### 29.7.2 验收测试编号

| 编号 | 描述 |
|------|------|
| T-OBS-V3-METRICS-01 | 切换时间范围后图表数据正确更新（Zustand 状态驱动） |
| T-OBS-V3-METRICS-02 | 图表在 HiDPI 屏幕下清晰（ECharts 内置 devicePixelRatio 支持） |
| T-OBS-V3-METRICS-03 | 无数据时显示"数据采集中"占位，不崩溃 |
| T-OBS-V3-METRICS-04 | 四种图表类型均正确渲染 |
| T-OBS-V3-METRICS-05 | 降采样后数据点数量 ≤1000 |

---

### 29.8 后端 API 扩展契约（M18：§OBS-V3-API-001~004）

覆盖需求：FR-OBS-008~011。
回归口径：T-OBS-HEALTH-01~06、T-OBS-SESS-01~06、T-OBS-TRACE-01~08、T-OBS-TS-01~06。

#### 29.8.1 全服务健康聚合 API（§OBS-V3-API-001）

**端点**：`GET /obs/services/health`

**功能**：并发探测双端所有已注册服务健康状态；单服务超时（≤200ms）不阻塞其他服务；支持 `?side=host|edge` 过滤。

**已注册服务（最小集）**：
- Host 侧（8 个）：LLM Gateway、Neo4j、SQLite、Chroma、Scheduler、Bond Engine、Memory Distiller、MCP Provider
- Edge 侧（5 个）：ASR、TTS、VTS、Audio Playback、Edge RPC

**响应结构（最小字段，语义固定）**：
```json
{
  "ts_ms": 1709500000000,
  "total": 13,
  "healthy": 12,
  "degraded": 1,
  "down": 0,
  "services": [
    {
      "service_id": "neo4j",
      "service_name": "Neo4j Bond SSOT",
      "side": "host",
      "status": "UP",
      "latency_ms": 12,
      "last_checked_ts_ms": 1709500000000,
      "error_summary": null
    }
  ]
}
```

**约束**：
- 响应时间 ≤500ms（并发探测）。
- 响应结构稳定（字段不随服务状态变化而缺失）。
- 单服务超时时该服务 `status=DOWN`，其余正常返回。

**落点**：
- `host_brain/app/obs_routes.py`（新增端点）
- `host_brain/obs/service_health_aggregator.py`（ServiceHealthAggregator）
- `tests/unit/test_service_health_aggregator.py`（T-OBS-HEALTH-01~06）

#### 29.8.2 会话列表 API（§OBS-V3-API-002）

**端点**：`GET /obs/sessions`

**查询参数**：`start_time`、`end_time`、`status`、`channel`、`keyword`、`page`、`page_size`

**响应结构（最小字段）**：
```json
{
  "total_count": 100,
  "page": 1,
  "page_size": 20,
  "results": [
    {
      "request_id": "01HXXX",
      "ts_ms": 1709500000000,
      "status": "APPROVE",
      "duration_ms": 1234,
      "channel": "wechat",
      "reason_code": null
    }
  ]
}
```

**约束**：
- 空结果时返回 `results=[]` 而非 404。
- `total_count` 与实际数据一致。
- 过滤参数组合正确生效。

**落点**：
- `host_brain/app/obs_routes.py`（新增端点）
- `host_brain/obs/session_query.py`（SessionQueryService）
- `host_brain/audit/migrations/0019_obs_sessions_index.sql`（查询索引）
- `tests/unit/test_session_query.py`（T-OBS-SESS-01~06）

#### 29.8.3 链路节点详情 API（§OBS-V3-API-003）

**端点**：`GET /obs/trace/{request_id}`

**查询参数**：`include_io`（默认 true；false 时不返回 input/output 字段）

**节点结构（最小字段，语义固定）**：
```json
{
  "request_id": "01HXXX",
  "nodes": [
    {
      "node_id": "node_001",
      "node_type": "LLM",
      "node_name": "core_reasoner_primary",
      "status": "SUCCESS",
      "start_ts_ms": 1709500000000,
      "end_ts_ms": 1709500001500,
      "duration_ms": 1500,
      "input": {},
      "output": {},
      "truncated": false,
      "reason_code": null,
      "parent_node_id": null,
      "side": "host",
      "model_id": "gpt-4o"
    }
  ]
}
```

**约束**：
- 不存在的 `request_id` 返回 404 + 稳定错误结构。
- `input`/`output` 超 64KB 截断并标注 `truncated=true`。
- `parent_node_id` 正确反映执行树结构。
- 数据来源：从审计链（intent_log / governor_log / skill_exec_log / asr_log / tts_log / vts_log 等）聚合构建。

**落点**：
- `host_brain/app/obs_routes.py`（新增端点）
- `host_brain/obs/trace_node_builder.py`（TraceNodeBuilder）
- `tests/unit/test_trace_node_builder.py`（T-OBS-TRACE-01~08）

#### 29.8.4 指标时序 API（§OBS-V3-API-004）

**端点**：`GET /obs/metrics/timeseries`

**查询参数**：`metrics`（逗号分隔，支持批量）、`interval`（1m/5m/1h）、`start_time`、`end_time`

**支持的指标维度**：`request_count`、`reject_rate`、`latency_p50`、`latency_p95`、`reason_code_dist`

**响应结构（最小字段）**：
```json
{
  "interval": "5m",
  "start_time": "2026-03-04T00:00:00Z",
  "end_time": "2026-03-04T06:00:00Z",
  "metrics": {
    "request_count": {
      "data_points": [
        {"ts_ms": 1709500000000, "value": 42}
      ]
    },
    "reject_rate": {
      "data_points": [
        {"ts_ms": 1709500000000, "value": 0.05}
      ]
    }
  }
}
```

**约束**：
- 数据点上限 1000（超出自动降采样）。
- 无数据区间返回 `null` 值点保证时序连续性。
- 批量查询返回 `{metrics: {metric_name: {data_points: [...]}}}` 结构。

**落点**：
- `host_brain/app/obs_routes.py`（新增端点）
- `host_brain/obs/metrics_timeseries.py`（MetricsTimeseriesService）
- `tests/unit/test_metrics_timeseries.py`（T-OBS-TS-01~06）

---

### 29.9 M17/M18 新增错误码汇总

| 错误码 | 触发场景 |
|--------|---------|
| `OBS_SERVICE_HEALTH_TIMEOUT` | 单服务健康探测超时（≤200ms 硬限） |
| `OBS_SESSION_QUERY_INVALID` | 会话列表查询参数非法（时间范围/分页越界） |
| `OBS_TRACE_NOT_FOUND` | 指定 request_id 不存在于审计链 |
| `OBS_TRACE_IO_TRUNCATED` | 节点 input/output 超 64KB 被截断（非错误，标注字段） |
| `OBS_METRICS_INTERVAL_INVALID` | 时序查询 interval 参数非法（非 1m/5m/1h） |
| `OBS_METRICS_DOWNSAMPLE_APPLIED` | 数据点超 1000 触发降采样（非错误，标注字段） |

---

### 29.10 M17/M18 发布清单质量门禁

**M17 质量门禁**：

| 门禁项 | 要求 |
|--------|------|
| `release_ready` | 必须为 true |
| `all_pages_functional` | 四页面全量可用（Overview/Sessions/Trace/Metrics） |
| `zero_js_errors` | 浏览器控制台无 JS 错误 |
| `design_review_passed` | 符合 Dify 简约白色风格 |

**M18 质量门禁**：

| 门禁项 | 要求 |
|--------|------|
| `release_ready` | 必须为 true |
| `all_api_endpoints_tested` | 4 个新增端点测试全部通过 |
| `frontend_api_integration_verified` | 前端切换真实 API 验证通过 |
| `zero_regressions` | failed=0 |
| `total_tests_passed` | ≥ 1575 |

