受众：MASTER

标题：产品需求文档（PRD）
# Natsume Ai 产品需求文档（PRD）
版本：v1.0（可验收基线）  
基线白皮书：v1.0（意识主权架构）  
适用架构：Host_Brain（Python） + Dify（LLM Agent） + Natsume_Body（Edge） +（可选）Cloud Exocortex + ClawdBot

---

## 0. 文档目的与使用方式

本 PRD 的目标是把白皮书中的架构与约束，转译为“可编号、可验收、可回归”的产品需求条目。  
实现细节（模块边界、接口、Schema、日志、测试落地方式）以《开发实现规格（DevSpec）》为准；白皮书负责“宪法/不可破坏约束”；理论文档负责“原理与推导”。

本 PRD 的强制使用规则（面向实现与验收，而非工具）：
1) 每个代码改动必须引用 1+ 个需求编号（例如 FR-GOV-003），并给出明确验收方法（命令/用例/断言）。  
2) 任何“执行事实/写入事实/确认事实”的对外表述，必须可追溯到 Host 的审计链摘要字段；否则视为缺陷。  
3) 对外输出结构必须稳定：成功/拒绝/降级都必须返回可解析结构，并带可统计的拒绝码/故障码（reason_code）。

---

## 1. 产品定义

### 1.1 一句话定义
Natsume Ai 是一个“意识主权”的本地智能体：LLM 负责叙事与意图草案，Host 负责裁决与执行，所有动作必须票据化并可审计回放。

### 1.2 用户与使用场景
- 主用户：Master（本地开发者），在 Host 机器上调试与运行；Edge 负责“身体”侧能力（语音/视觉/微信接收/头像驱动）。
- 场景 S1：日常对话与陪伴（不触发执行）
- 场景 S2：开发协作（读取日志、生成修复建议、产出工单/文档；默认不直接改代码）
- 场景 S3：低风险可逆干预（桌面留言、生成本地文件产物）
- 场景 S4：需要 UI 自动化的任务（ClawdBot）：必须视觉审计 + 红线熔断
- 场景 S5：依赖不可用/故障时：系统仍需稳定降级与可观测

### 1.3 成功标准（可度量）
- SM1 审计完整率：≥99% 的请求可串联追溯（user_input → intent → governor → token → skill_exec → result）。  
- SM2 违规输出率：伪执行宣告/越权承诺/Canon 当事实引用合计 ≤0.1%（在回归集上）。  
- SM3 拒绝可解释率：decision=REJECT 的输出中，reason_code 缺失率 = 0（硬约束）。  
- SM4 稳定性：任意依赖失败/超时不导致 Host 输出崩溃；必须返回可解析结构（硬约束）。  
- SM5 可撤销覆盖：所有 reversible=true 的能力，撤销成功率 ≥95%（测试集 + 手工抽检）。

---

## 2. 范围与非目标

### 2.1 v1.0 范围（MVP→可运行闭环）
- 语言→意图：Dify 生成叙事 + Intent 草案；Host 负责解析与严格校验。  
- 裁决→执行：Governor gate + tokenized execution；Skills 统一入口；可观测审计链。  
- 成长闭环：Bond SSOT（Neo4j）+ clearance/trust snapshot（SQLite 派生缓存）+ Episodic（向量）+ Style Canon（Host 主权）。  
- 降级与故障：阶段化状态机 + 统一故障码 + 面向用户的克制提示。  
- LSP：Artifact Vault + Undo Token + manifest + GC（可审计）。  
- 视觉红线：视觉熔断与 UI 定位置信度门槛。

### 2.2 明确非目标（v1.0 不做）
- 不做跨用户多租户与云端账号体系。  
- 不做高风险自动化（系统配置大规模修改/支付/账号绑定）；仅允许拒绝或引导。  
- 不把剧情当事实的“世界观知识库”；Canon 仅作为风格范式库（tone_only）。  
- 不把 Dify 作为主权存储；Dify 只能消费 Host context_pack 并产出 intent_draft。

---

## 3. 用户可感知流程（黑盒口径）

### 3.1 普通对话（不执行）
1) 用户输入  
2) Host 读取 Hard Snapshot + Bond Summary +（可选）Episodic/Canon 召回  
3) Host 打包 context_pack → Dify  
4) Dify 输出 narrative + intent_draft（可为空）  
5) Host QA（伪执行/语言/Canon-as-fact/格式漂移）  
6) Host 写入审计与历史（失败也需可观测记录）  
7) 输出给用户：稳定结构 + narrative +（可选）intent 草案/裁决结果摘要

用户体验目标：回复稳定、像她、但不会“假装已经做了”。

### 3.2 执行请求（需要 Skill）
1) Dify 提出 intent_draft  
2) Host：lenient parse → strict validate → governor adjudicate  
3) 若 APPROVE：下发 approval_token，执行 Skill，记录 skill_exec_log  
4) 若 REJECT：返回 reason_code + user_facing_hint，并记录 system_event  
5) 用户看到的“执行结果”只能来自审计摘要字段（而非叙事自述）

### 3.3 高风险确认（confirm_token）
- 对 risk_level=4 / irreversible / account/payment/system-settings：必须两步确认  
  - 第一次：只返回 CONFIRM_REQUIRED + 引导（不执行）  
  - 第二次：携带 confirm_token 才可能 APPROVE

### 3.4 可撤销动作（undo_token + manifest）
- 对 reversible=true 的动作：默认必须产出 manifest + artifacts，并返回 undo_token  
- Undo 本身也走 Governor（较低风险）  
- 删除默认 move-to-quarantine；真删除必须 confirm_token

### 3.5 故障降级（用户体验口径）
- 依赖失败/超时：仍返回可解析结构 + 降级提示（克制，不暴露内部组件名）  
- 降级最多追问 1 个关键问题  
- 不产生“需要执行才能成立”的承诺句

---

## 4. 功能需求（Functional Requirements）

说明：每条需求必须具备“描述 + 关键约束 + 验收标准”。  
命名约定：FR-域-编号（域：CORE / INTENT / GOV / TOKEN / SKILL / AUDIT / AIRLOCK / VISION / BOND / MEMORY / CANON / MODE / LSP / UX / OBS）

### 4.1 核心链路与输出稳定（CORE）

FR-CORE-001 单向链强制  
- 描述：执行路径必须严格为 Dify(intent草案) → Host(governor) → Skill(exec) → Audit。  
- 关键约束：任何技能执行必须经由 Skills Loader；不得旁路。  
- 验收：旁路尝试被拒绝并记录；无法绕过 token 校验触发实际执行。

FR-CORE-002 对外输出结构稳定  
- 描述：成功/拒绝/降级均必须输出稳定 JSON 结构（字段可为空，结构不可变）。  
- 验收：故障注入（DB down / LLM timeout / parse fail）下输出永远可 JSON.parse；并含 quality.stage 与 reason_code（若适用）。

FR-CORE-003 叙事与事实分离  
- 描述：叙事层不得自造执行事实；执行事实只来自审计链摘要字段。  
- 验收：回归集中出现“已执行/已保存/已确认”等事实句时，必须可指向审计摘要字段；否则 QA 拦截并重写/降级。

### 4.2 Intent 规范与校验（INTENT）

FR-INTENT-001 固定 Schema 与 allowlist  
- 描述：Host 只接受固定 Schema（schema_version、id、ts_ms、session_id、seq、type、action、target、args、nonce）。type/action/target 必须 allowlist。  
- 验收：缺字段/枚举越界/类型错误 → REJECT 且 reason_code 稳定（INTENT_SCHEMA_INVALID / INTENT_ARGS_INVALID）。

FR-INTENT-002 两阶段校验  
- 描述：先宽松解析修复格式漂移，再严格校验；不得臆造字段或补语义。  
- 验收：宽松解析仅修 JSON 语法；严格校验失败给出明确 reason_code；不得出现“自动补全 action/target/args”。

FR-INTENT-003 重放与顺序保护  
- 描述：intent_id 去重、seq 单调、ts 过期窗口、nonce 缓存。  
- 验收：重复 id → REPLAY_DETECTED；seq 乱序/回滚 → SEQ_INVALID；过期 → EXPIRED_INTENT。

### 4.3 Governor 裁决与拒绝码（GOV）

FR-GOV-001 Gate 体系可配置且可审计  
- 描述：至少包含 Trust/Energy/SysMode/RateLimit/Scope/Safety/Airlock Gate；规则版本化。  
- 验收：每次裁决输出 rule_trace，能定位阈值与快照摘要；同输入可回放得到一致结论。

FR-GOV-002 统一拒绝码强制  
- 描述：decision=REJECT 必须携带 reason_code（稳定枚举）；否则拦截并记录 GOVERNOR_OUTPUT_INVALID。  
- 验收：对外输出中“缺 reason_code 的拒绝”不可出现；内部日志可统计拦截次数。

FR-GOV-003 APPROVE 必有 approval_token_ref  
- 描述：没有 token_ref 的批准不允许进入执行；Skills 必须拒绝。  
- 验收：模拟 governor 漏发 token_ref，Skills 仍拒绝并记录 SKILL_TOKEN_MISSING。

### 4.4 Tokenized Execution（TOKEN）

FR-TOKEN-001 approval_token 绑定约束  
- 描述：token 必须绑定 intent_id、skill_name、session_id/seq、短 TTL，且带签名。  
- 验收：换 skill 复用/换 intent 复用/过期 token 全部失败（TOKEN_INVALID）。

FR-TOKEN-002 confirm_token 不可用语言伪造  
- 描述：confirm_token 只能由 Host 侧确认流程产生，不接受语言确认。  
- 验收：risk=4 无 confirm_token 必拒绝（CONFIRM_REQUIRED）；“我确认了”不改变裁决。

FR-TOKEN-003 undo_token + manifest 作为可撤销契约  
- 描述：reversible=true 的执行必须产生 undo_token 与 manifest_ref；未产出视为不合规。  
- 验收：缺 manifest_ref 的 reversible 执行被拒绝或升级 confirm；撤销可回放。

### 4.5 Skills 体系（SKILL）

FR-SKILL-001 SkillMeta 强制声明  
- 描述：每个 skill 必须声明 clearance_level、risk_level、scopes、reversible、requires_visual_audit、requires_confirm_token、allow/deny list 等。  
- 验收：注册缺字段直接失败；运行时加载一致性检查可观测。

FR-SKILL-002 统一执行入口  
- 描述：业务代码不得直接 import impl；只能通过 loader/registry 执行，并强制 token 校验。  
- 验收：CI 静态扫描 + 运行时断言双生效；违规 import 触发失败。

FR-SKILL-003 幂等与重试边界  
- 描述：同一 intent_id 执行必须幂等（复用结果或拒绝重复），避免重复副作用。  
- 验收：重复触发同一 intent_id 不产生第二次真实动作；日志可证明。

### 4.6 审计链与回放（AUDIT）

FR-AUDIT-001 三联画日志（Triptych）  
- 描述：intent_log / governor_log / skill_exec_log 必须完整，并可串联。  
- 验收：任意 request_id 可追溯三段日志并字段对齐（intent_id、token_ref）。

FR-AUDIT-002 视觉与气闸日志对齐  
- 描述：clawd_action_log / airlock_log 必须引用 intent_id/request_id，并可回放证据（截图 hash/报告 digest）。  
- 验收：UI 自动化每步都有 before/after 截图引用；外发每次都有净化报告与 digest。

FR-AUDIT-003 QA 违规记录  
- 描述：伪执行宣告、Canon-as-fact、语言政策违规等必须进入 qa_violation_log。  
- 验收：回归触发一次违规，可在日志面板检索并回放原文与处理结果。

### 4.7 外脑气闸（AIRLOCK）

FR-AIRLOCK-001 出站净化报告  
- 描述：外发 payload 必须最小化并生成 sanitization_report；不得外发硬状态原始值/宪法全文/规则细节。  
- 验收：每次外发都有 removed_fields/redactions/payload_digest；缺失则拒绝（AIRLOCK_BLOCKED_EGRESS）。

FR-AIRLOCK-002 入站隔离与分类  
- 描述：外部返回内容必须分类（FACT/DRAFT_CODE/DRAFT_INSTRUCTION/UNKNOWN），不得直通执行。  
- 验收：外部内容触发执行前必须被重构为 Intent 并通过 Governor；否则阻断（EXTERNAL_DATA_GATE）。

### 4.8 视觉熔断与 ClawdBot（VISION）

FR-VISION-001 视觉红线熔断  
- 描述：支付/系统设置/删除/账号等红线场景，门禁不足必须拒绝（VISUAL_REDLINE）。  
- 验收：红线场景除非满足更高门禁 + 二次确认，否则无法继续点击/输入。

FR-VISION-002 定位置信度门槛  
- 描述：UI 元素定位 confidence < 0.80 必须停止并返回 UI_ELEMENT_NOT_FOUND。  
- 验收：模拟低置信度定位，不发生任何点击；clawd_action_log 记录完整。

### 4.9 关系系统（BOND）

FR-BOND-001 Neo4j 作为 SSOT  
- 描述：Bond 六维在 Neo4j，任何组件不得双写；SQLite 仅存 snapshot 派生缓存。  
- 验收：除 bond_dynamics 外写 Neo4j 必失败；snapshot 写入可追溯 bond_update_log。

FR-BOND-002 Event 驱动更新  
- 描述：Bond 变化只能由事件驱动，事件必须含 etype、intensity、evidence_refs。  
- 验收：缺 evidence_refs 的 bond 更新被拒绝并记录 BOND_UPDATE_INVALID。

FR-BOND-003 给 Dify 的 Bond Summary 降采样  
- 描述：Dify 不得获得数值；只拿 stage/trend/capability_hint。  
- 验收：Dify 输出出现“信任值=xx”等数值宣称，QA 必拦截。

### 4.10 记忆系统（MEMORY）

FR-MEM-001 Episodic 写入门禁与证据链  
- 描述：写入必须有 evidence_refs；默认不写入“纯叙事输出”。  
- 验收：无证据写入失败；日志可回放触发原因。

FR-MEM-002 衰减、合并、删除可审计  
- 描述：条目有 decay_profile；合并保留证据链；删除需 intent+governor。  
- 验收：合并后原条目保留 merged_into；删除能追溯原因与流程。

### 4.11 风格骨架库（CANON）

FR-CANON-001 Canon 仅用于口吻，不得覆盖事实  
- 描述：Canon 条目必须标记 tone_only/unsafe_as_fact；召回与注入字段隔离。  
- 验收：Canon 被当事实引用时，QA 拦截并记 CANON_AS_FACT_VIOLATION。

FR-CANON-002 Host 主权与更新流程  
- 描述：Canon SSOT 在 Host；更新必须走 Evolution/白名单 + 审计。  
- 验收：Dify 无法直接写 Canon；任何 Canon 更新都有提案与审计记录。

### 4.12 sys_mode 与路由（MODE）

FR-MODE-001 三模式行为差异  
- 描述：Normal/Gaming/Sleep 影响能力矩阵、输出节奏、资源预算。  
- 验收：Gaming 禁止 clawd_ui_action；Sleep 禁止 CONTROL 类执行；拒绝码 SYSMODE_FORBIDDEN。

FR-MODE-002 来源可审计  
- 描述：sys_mode 切换必须有来源证据（观测/用户授权/定时策略）。  
- 验收：每次切换写 hard_state_log（原因、证据摘要），可回放。

### 4.13 LSP（本地沙盒剖面）（LSP）

FR-LSP-001 Artifact Vault 可寻址可 GC  
- 描述：截图/关键帧/回滚材料进入 Vault，具 hash 与 policy_profile_id；GC 可审计且不得删有效 undo 窗口内材料。  
- 验收：GC 后仍可证明未删有效回滚材料；vault_gc_log 完整。

FR-LSP-002 manifest 作为回滚清单  
- 描述：可逆变更产出 manifest（before/after/path/hash/refs/rollback_steps/digest）。  
- 验收：manifest digest 不匹配时，Undo 必拒绝。

### 4.14 交互与故障处理（UX）

FR-UX-001 阶段化状态机与降级  
- 描述：S0..S8 每阶段有失败分支与 reason_code；降级最多追问 1 个关键问题。  
- 验收：注入任意阶段异常，输出不崩；quality.stage 反映失败点；提示克制一致。

FR-UX-002 Host 最小叙事模板库  
- 描述：LLM 不可用/不合规时，Host 输出最低风格且不伪执行的模板（不暴露内部组件名）。  
- 验收：关闭 LLM 或强制超时，仍输出合规模板；不产生执行 intent。

### 4.15 可观测与运维（OBS）

FR-OBS-001 关键指标与面板  
- 描述：展示拒绝原因分布、伪执行命中次数、召回概览、Hard Snapshot、最近链路摘要。  
- 验收：本地运行可一键查看；日志字段对齐 request_id/intent_id。

FR-OBS-002 开发/生产模式差异  
- 描述：开发模式可显示内部 debug trace；生产模式默认隐藏，仅留 user_facing_hint。  
- 验收：切换模式后输出差异符合预期；不影响裁决与审计事实。

---

## 5. 非功能需求（NFR）

NFR-SEC-001 本地优先与最小暴露  
- 要求：默认本地运行；对外通信必须经 AIRLOCK；敏感字段不得出站。  
- 验收：出站净化报告覆盖率=100%；抽检出站 payload 不含硬状态原值与规则细节。

NFR-REL-001 降级可用性  
- 要求：依赖故障不崩溃；输出结构稳定；关键链路可观测。  
- 验收：故障注入矩阵全通过（DB/Neo4j/Chroma/Dify/Edge 超时或不可用）。

NFR-PERF-001 响应时延预算（本地目标）  
- 要求：普通对话可接受；执行链路可解释；超时可控并可降级。  
- 验收：在基准机器上给出 p50/p95 统计（由 DevSpec 定义具体阈值与采集方式）。

NFR-MAINT-001 可回归与可迁移  
- 要求：需求编号→测试→日志字段一致；升级不破坏审计可回放。  
- 验收：版本升级后回放旧 intent 仍可得到一致裁决或明确迁移提示。

---

## 6. 需求可追溯性（PRD ↔ DevSpec ↔ Tests）

要求：
- 每条 FR 必须在 DevSpec 中有对应实现章节（接口/Schema/日志/测试落地）。  
- 每条 FR 至少对应 1 条可自动化回归测试（或最小合规测试编号 T-xx）。  
- CI 必须具备：静态扫描（禁止旁路）+ 单元/集成/故障注入测试。

说明：
- 映射表由 `docs/60_trace/requirements_trace.md`（或等价）维护；PRD 只定义规则与编号体系。

---

## 7. 里程碑（按“可验收能力包”交付）

M1 主权闭环（最小可运行）：FR-CORE/INTENT/GOV/TOKEN/SKILL/AUDIT（基础）  
M2 成长闭环（关系+记忆+风格）：FR-BOND/MEM/CANON  
M3 LSP（可撤销默认）：FR-LSP + 可逆能力接入  
M4 体验包（低风险）：桌面留言/梦境/Focus Mode（均需 SkillMeta + 审计）  
M5 视觉自动化（高门槛）：FR-VISION 全量验收  
M6 Edge 深化：语音/头像/微信红线治理（作为受治理 Skills）

每个里程碑交付必须包含：
- 完成的需求编号清单  
- 验收脚本/回归测试清单  
- 风险回顾（新增 scope / 票据 / 红线变化）  
- 文档更新（PRD/DevSpec/白皮书差异说明）

---

## 附录 A：PRD 写作与验收模板（供持续新增需求时使用）

新增需求必须包含：
- 用户故事：作为……我希望……（可选，但建议保留）  
- 输入/输出（黑盒）：输入是什么；输出结构是什么  
- 约束：风险等级、门禁、模式影响（Normal/Gaming/Sleep）、是否可撤销  
- 验收标准：至少覆盖 3 类用例  
  1) 正向成功  
  2) 门禁/拒绝（reason_code 断言）  
  3) 故障降级（结构稳定 + stage + reason_code）

建议每个需求条目落地后，都在 Trace 表中写：
- FR-xxx ↔ DevSpec 章节 ↔ 测试编号（T-xx） ↔ 日志字段/表
