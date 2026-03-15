受众：MASTER
标题：产品需求文档（PRD）

# Natsume Ai 产品需求文档（PRD）
版本：v1.4.1（可验收基线）
基线白皮书：v1.4（意识主权宪法与人格设定）
适用架构：Host_Brain（Python，含 LangGraph 编排） + Natsume_Body（Edge） + Cloud Exocortex + ClawdBot

---

## 0. 文档目的与使用方式

本 PRD 的目标是把白皮书中的架构与约束，转译为“可编号、可验收、可回归”的产品需求条目。
实现细节（模块边界、接口、Schema、日志、测试落地方式）以《开发实现规格（DevSpec）》为准；白皮书负责“宪法/不可破坏约束”；理论文档负责“原理与推导”。

本 PRD 的强制使用规则（面向实现与验收，而非工具）：
1) 每个代码改动必须引用 1+ 个需求编号（例如 FR-GOV-003），并给出明确验收方法（用例/断言/测试编号）。
2) 任何“执行事实/写入事实/确认事实”的对外表述，必须可追溯到 Host 的审计链摘要字段；否则视为缺陷。
3) 对外输出结构必须稳定：成功/拒绝/降级都必须返回可解析结构，并带可统计的拒绝码/故障码（reason_code）。

### 0.1 PRD、产品文档与任务列表的关系（强制口径）

本项目文档分层固定为：白皮书（法律）→ PRD（需求）→ DevSpec（实现规格）→ DevTasks（任务卡）→ 测试与审计回放（证据）。

1）白皮书（docs/10_whitepaper/）
- 定义不可变边界、不变量、真相层级与叙事禁令。
- 只回答“什么永远不允许/什么算事实”，不回答“做什么/怎么做”。

2）PRD（本文件）
- 定义“做什么 + 验收”，并以 FR-域-编号（例如 FR-GOV-003）编号。
- 任何需求必须可验收（输入/输出/观测点），但不得写实现步骤与工程落点细节。

3）DevSpec（docs/30_devspec/DevSpec.md）
- 定义“怎么做 + 如何验证（测试点/日志点/契约点）”。
- 负责把 FR-… 映射到：模块边界、Schema、reason_code、审计字段、测试编号 T-… 等可执行工程契约。

4）DevTasks（docs/30_devspec/DevTasks.md）
- DevTasks 是“任务列表”，以 TASK-编号 表示最小可交付工作单元。
- 每条 TASK 必须反向引用：覆盖哪些 FR-…、落在 DevSpec 哪些章节、对应哪些测试编号 T-…（验收断言）。
- 任务列表不应从 PRD/白皮书直接生成；必须从 DevSpec 的工程契约拆解而来（避免把“法律/需求”误当“实现细节”）。

5）Trace（docs/60_trace/requirements_trace.md）
- 用于审阅覆盖关系：白皮书/PRD → DevSpec → DevTasks → Tests → Invariants。
- Trace 只做“指向”，不承载实现内容。

### 0.2 需求成熟度与进入开发门槛（DoR/DoD）

本项目的“需求是否可开发”不是主观判断，而是看追溯链是否闭合。
DoR（Definition of Ready：进入开发门槛）
- FR-… 需求条目具备明确的“输入/输出/拒绝条件（含 reason_code）/验收标准”。
- 需求不依赖实现细节措辞（避免把“怎么做”写进 PRD）。
- 需求已在 Trace 中登记，并标记覆盖状态（至少达到“PRD_READY”）。

DoD（Definition of Done：完成门槛）
- DevSpec 对应章节已给出工程契约：Schema、审计字段、拒绝码映射、日志点、（必要时）静态扫描断言。
- 该 FR 至少绑定 1 个测试编号 T-…（回归断言明确）。
- Trace 状态至少达到“SPEC_READY”；当任务卡与测试用例齐备后可进入“TASK_READY/TEST_READY”。

---

## 1. 产品定义

### 1.1 一句话定义
Natsume Ai 是一个“意识主权”的本地智能体：语义内核（LLM）负责叙事与意图草案，Host 负责裁决与执行，所有动作必须票据化并可审计回放。

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
- 语言→意图：语义内核生成叙事 + Intent 草案；Host 负责解析与严格校验。
- 裁决→执行：Governor gate + tokenized execution；Skills 统一入口；可观测审计链。
- 成长闭环：Bond SSOT（Neo4j）+ clearance/trust snapshot（SQLite 派生缓存）+ Episodic（向量）+ Style Canon（Host 主权）+ World Canon（Host 主权最小权威内容集合，用于一致性引用；非执行事实）。
- 降级与故障：阶段化状态机 + 统一故障码 + 面向用户的克制提示。
- LSP：Artifact Vault + Undo Token + manifest + GC（可审计）。
- 视觉红线：视觉熔断与 UI 定位置信度门槛。
- Flow 配置资产：LangGraph Flow（图/配置）版本化与环境指针（dev/staging/prod）可追溯。

### 2.2 明确非目标（v1.0 不做）
- 不做跨用户多租户与云端账号体系。
- 不做高风险自动化（系统配置大规模修改/支付/账号绑定）；仅允许拒绝或引导。
- 不做把“剧情/叙事”当执行事实的世界观大库；仅维护最小 World Canon（系统术语/设定/关键不变量），用于一致性引用与冲突裁决，但永远不具备执行事实效力；Style Canon 仍仅用于风格范式（tone_only）。
- 不把任何 LLM 组件作为主权存储；语义内核只能消费 Host context_pack 并产出 intent_draft（草案）。

### 2.3 系统服务清单（必须实现，缺一不可）
说明：以下为本项目必须实现的“服务项清单”。允许存在“运行时暂不可用/未配置/降级”的情况，但必须满足：
- 接口与健康状态可观测；
- 降级必须返回稳定结构与可统计的 reason_code / stage；
- 不得伪造“已执行/已保存/已确认”的事实口径。

Host 端（主权与中枢）
- 编排入口服务（Host Orchestrator API：对外 chat 入口 + 事件汇聚）
- Governor / Rule Engine（唯一裁决点）
- Token Service（approval/confirm/undo 票据签发与校验）
- Audit / Ledger Writer（审计写入器）
- SQLite Ledger（最小事实账本）
- Neo4j Bond SSOT（关系域 SSOT）
- Episodic Vector Store（向量库）
- 文本嵌入模型服务（Embedding）
- 重排模型服务（Reranker）
- 召回与融合服务（Recall/Fusion：TopK evidence pack）
- 屏幕采集与屏幕监控服务（Screen Capture/Monitor）
- OCR/识图服务（按需推理）
- 摄像头采集与识别服务（Camera Capture + Vision Inference）
- ClawdBot（视觉 UI 代理：计划草案 + 红线熔断）
- Cloud LLM Gateway + AIRLOCK（外部模型出入口与脱敏隔离）
- Observability（日志/指标/追踪与面板）

Edge 端（身体侧能力）
- Edge RPC Server（Host↔Edge 调用与事件上送）
- ASR 服务（faster-whisper large-v3，流式/分段）
- TTS 服务（GPT-SoVITS，全 GPU 常驻）
- Audio Playback / Speaker Queue（播报队列与可打断）
- VTS / Live2D 控制服务（VTube Studio talk gate + 表情映射）
- Edge 兜底小模型服务（Ollama 3B 量化，默认卸载，低频触发）
- Edge 资源闸门服务（GPU/音频任务互斥与优先级：ASR>播放>TTS合成>兜底LLM）

---

## 3. 用户可感知流程（黑盒口径）

### 3.1 普通对话（不执行）
1) 用户输入
2) Host 读取 Hard Snapshot + Bond Summary +（可选）Episodic/Canon 召回
3) Host 打包 context_pack → 语义内核（LLM）
4) 语义内核输出 narrative + intent_draft（可为空）
5) Host QA（伪执行/语言/Canon-as-fact/格式漂移）
6) Host 写入审计与历史（失败也需可观测记录）
7) 输出给用户：稳定结构 + narrative +（可选）intent 草案/裁决结果摘要

用户体验目标：回复稳定、像她、但不会“假装已经做了”。

### 3.2 执行请求（需要 Skill）
1) 语义内核提出 intent_draft
2) Host：lenient parse → strict validate → governor adjudicate
3) 若 APPROVE：下发 approval_token，执行 Skill，记录 skill_exec_log
4) 若 REJECT：返回 reason_code + user_facing_hint，并记录 system_event
5) 用户看到的“执行结果”只能来自审计摘要字段（而非叙事自述）

### 3.3 冷启动与依赖不可用（No API Key / Service Temporarily Unavailable）
目标：在未配置云端 API Key 或任一服务暂不可用时，系统仍必须完成闭环：稳定输出 + 可观测降级 + 可回放审计索引。

黑盒流程（用户可感知）：
1. 用户输入
2. Host 识别当前不可用服务与能力矩阵（含 sys_mode/Gaming 门控）
3. 输出：稳定结构（可解析 JSON）+ 克制叙事（不得伪造“已执行/已保存/已确认”）+ 明确降级标记（quality.stage / reason_code）
4. 审计：至少写入 proposal/decision 的索引（执行可为空）

验收：
- 无 API Key：必须输出可解析结构，且 quality.stage 明确体现降级点；不得出现伪执行宣告。
- 任一服务暂不可用：必须输出可解析结构；若拒绝则 reason_code 必须稳定可统计（同版本同类缺失同码）。
- 可回放：至少能用 request_id 串联到 intent_draft（可为空）与 governor decision（不可为空）。
- 不得把“服务不可用/未触发”当作“事实为空”的结论输出（例如不得把“向量库不可用”说成“我没有任何相关记忆”）。

### 3.4 高风险确认（confirm_token）
- 对 risk_level=4 / irreversible / account/payment/system-settings：必须两步确认
  - 第一次：只返回 CONFIRM_REQUIRED + 引导（不执行）
  - 第二次：携带 confirm_token 才可能 APPROVE

### 3.5 可撤销动作（undo_token + manifest）
- 对 reversible=true 的动作：默认必须产出 manifest + artifacts，并返回 undo_token
- Undo 本身也走 Governor（较低风险）
- 删除默认 move-to-quarantine；真删除必须 confirm_token

### 3.6 故障降级（用户体验口径）
- 依赖失败/超时：仍返回可解析结构 + 降级提示（克制，不暴露内部组件名）
- 降级最多追问 1 个关键问题
- 不产生“需要执行才能成立”的承诺句

---

## 4. 功能需求（Functional Requirements）

说明：每条需求必须具备“描述 + 关键约束 + 验收标准”。
命名约定：FR-域-编号（域：CORE / FLOW / INTENT / GOV / TOKEN / SKILL / AUDIT / AIRLOCK / VISION / BOND / MEMORY / CANON / MODE / LSP / UX / OBS）

### 4.1 核心链路与输出稳定（CORE）

FR-CORE-001 单向链强制
- 描述：执行路径必须严格为 语义内核(intent草案) → Host(governor) → Skill(exec) → Audit。
- 关键约束：任何技能执行必须经由 Skills Loader；不得旁路。
- 验收：旁路尝试被拒绝并记录；无法绕过 token 校验触发实际执行。

FR-CORE-002 对外输出结构稳定
- 描述：成功/拒绝/降级均必须输出稳定 JSON 结构（字段可为空，结构不可变）。
- 验收：故障注入（DB down / LLM timeout / parse fail）下输出永远可 JSON.parse；并含 quality.stage 与 reason_code（若适用）。

FR-CORE-003 叙事与事实分离
- 描述：叙事层不得自造执行事实；执行事实只来自审计链摘要字段。
- 验收：回归集中出现“已执行/已保存/已确认”等事实句时，必须可指向审计摘要字段；否则 QA 拦截并重写/降级。

FR-CORE-004 冷启动可用性（无 API Key/外部依赖）
- 描述：在未配置任何云端 API Key、且外部依赖（例如向量库/图数据库/边缘端等）不可用的情况下，系统仍必须能完成“输入→（可降级叙事）→审计记录→稳定输出”的最小闭环。
- 关键约束：
  - 不得因依赖缺失导致输出不可解析或结构漂移；
  - 不得伪造“已执行/已保存/已确认”等完成性事实；
  - 对缺失依赖必须显式降级并可观测（stage 与 reason_code 可统计）。
- 验收：
  - 无云端 API Key：输出结构稳定，quality.stage 明确标注降级点；
  - 外部依赖不可用：输出结构稳定，decision=REJECT（或降级）时 reason_code 稳定为 DEPENDENCY_UNAVAILABLE（或更精确子类）；
  - Triptych 仍可回放：至少能在审计索引中定位到本次请求的 proposal/decision（执行可为空）。

### 4.2 Flow 配置资产（FLOW）

FR-FLOW-001 Flow 资产版本化与追溯
- 描述：Flow（LangGraph 图/配置）属于可部署配置资产，必须版本化管理，以支持行为变更可对照、可回滚、可审计追溯。
- 关键约束：
  - Flow 导出物必须归档在仓库固定位置（flows/exports），并可按环境唯一确定“当前生效版本”；
  - 必须存在一个“当前生效指针”（flows/active.pointer，按 dev/staging/prod）与一个“追溯索引”（docs/60_trace/flows/index.md，按环境列出当前生效版本与变更摘要）；
  - 指针与索引不得互相矛盾；任一缺失/不一致视为配置不合规。
- 验收：
  - 任意一次 Flow 变更，都能在仓库中定位到对应导出文件；
  - 可从“当前生效指针”与“追溯索引”唯一确定各环境生效版本；
  - 回滚时切换回旧导出文件后，索引可指回对应版本并保留变更摘要；
  - 对应回归测试编号：T-Flow-01、T-Flow-02（配置资产一致性）。

### 4.3 Intent 规范与校验（INTENT）

FR-INTENT-001 固定 Schema 与 allowlist
- 描述：Host 只接受固定 Schema（schema_version、id、ts_ms、session_id、seq、type、action、target、args、nonce）。type/action/target 必须 allowlist。
- 验收：缺字段/枚举越界/类型错误 → REJECT 且 reason_code 稳定（INTENT_SCHEMA_INVALID / INTENT_ARGS_INVALID）。

FR-INTENT-002 两阶段校验
- 描述：先宽松解析修复格式漂移，再严格校验；不得臆造字段或补语义。
- 验收：宽松解析仅修 JSON 语法；严格校验失败给出明确 reason_code；不得出现“自动补全 action/target/args”。

FR-INTENT-003 重放与顺序保护
- 描述：intent_id 去重、seq 单调、ts 过期窗口、nonce 缓存。
- 验收：重复 id → REPLAY_DETECTED；seq 乱序/回滚 → SEQ_INVALID；过期 → EXPIRED_INTENT。

### 4.4 Governor 裁决与拒绝码（GOV）

FR-GOV-001 Gate 体系可配置且可审计
- 描述：至少包含 Trust/Energy/SysMode/RateLimit/Scope/Safety/Airlock Gate；规则版本化。
- 验收：每次裁决输出 rule_trace，能定位阈值与快照摘要；同输入可回放得到一致结论。

FR-GOV-002 统一拒绝码强制
- 描述：decision=REJECT 必须携带 reason_code（稳定枚举）；否则拦截并记录 GOVERNOR_OUTPUT_INVALID。
- 验收：对外输出中“缺 reason_code 的拒绝”不可出现；内部日志可统计拦截次数。

FR-GOV-003 APPROVE 必有 approval_token_ref
- 描述：没有 token_ref 的批准不允许进入执行；Skills 必须拒绝。
- 验收：模拟 governor 漏发 token_ref，Skills 仍拒绝并记录 SKILL_TOKEN_MISSING。

### 4.5 Tokenized Execution（TOKEN）

FR-TOKEN-001 approval_token 绑定约束
- 描述：token 必须绑定 intent_id、skill_name、session_id/seq、短 TTL，且带签名。
- 验收：换 skill 复用/换 intent 复用/过期 token 全部失败（TOKEN_INVALID）。

FR-TOKEN-002 confirm_token 不可用语言伪造
- 描述：confirm_token 只能由 Host 侧确认流程产生，不接受语言确认。
- 验收：risk=4 无 confirm_token 必拒绝（CONFIRM_REQUIRED）；“我确认了”不改变裁决。

FR-TOKEN-003 undo_token + manifest 作为可撤销契约
- 描述：reversible=true 的执行必须产生 undo_token 与 manifest_ref；未产出视为不合规。
- 验收：缺 manifest_ref 的 reversible 执行被拒绝或升级 confirm；撤销可回放。

### 4.6 能力载体与工具平面（SKILL/TOOL）

#### 4.6.1 Skills 体系（SKILL）

FR-SKILL-001 SkillMeta 强制声明
- 描述：每个 skill 必须声明 clearance_level、risk_level、scopes、reversible、requires_visual_audit、requires_confirm_token、allow/deny list 等。
- 验收：注册缺字段直接失败；运行时加载一致性检查可观测。

FR-SKILL-002 统一执行入口
- 描述：业务代码不得直接 import impl；只能通过 loader/registry 执行，并强制 token 校验。
- 验收：静态扫描 + 运行时断言双生效；违规 import 触发失败。

FR-SKILL-003 幂等与重试边界
- 描述：同一 intent_id 执行必须幂等（复用结果或拒绝重复），避免重复副作用。
- 验收：重复触发同一 intent_id 不产生第二次真实动作；日志可证明。

#### 4.6.2 工具平面（TOOL）

FR-TOOL-001 工具元数据 Top-K 暴露（渐进式加载）
- 描述：每轮仅向语义内核暴露 tool_meta_pack（候选工具的最小元数据 Top-K 子集）；不得暴露全量工具列表与实现细节；WRITE 默认不进入可见集。
- 验收：同一轮上下文中工具元数据数量有硬上限；在未满足条件时 WRITE 工具不出现在 tool_meta_pack；违规触发可统计 reason_code（如 TOOL_VISIBILITY_POLICY_VIOLATION）。

FR-TOOL-002 tool_visibility_hash 追责
- 描述：Host 必须记录“该轮模型看见了哪些工具元数据”的 tool_visibility_hash，并与 intent_log/llm_call_log 关联，支持事后追责。
- 验收：每次产生 intent_draft 时，审计链可定位到 tool_visibility_hash；缺失则视为合规失败并拒绝进入执行路径。

FR-TOOL-003 ToolBroker 单入口与 WRITE Token gating
- 描述：所有工具执行必须经 ToolBroker 单入口；WRITE 类工具必须在 Governor=APPROVE 且 token 合法时才可执行，并产生 evidence_ref 串联到审计链。
- 验收：任何绕过 ToolBroker 的执行都被阻断；WRITE 无 token 必拒绝且可统计；成功执行必须生成可回放的 evidence_ref。

### 4.7 审计链与回放（AUDIT）

FR-AUDIT-001 三联画日志（Triptych）
- 描述：intent_log / governor_log / skill_exec_log 必须完整，并可串联。
- 验收：任意 request_id 可追溯三段日志并字段对齐（intent_id、token_ref）。

FR-AUDIT-002 视觉与气闸日志对齐
- 描述：clawd_action_log / airlock_log 必须引用 intent_id/request_id，并可回放证据（截图 hash/报告 digest）。
- 验收：UI 自动化每步都有 before/after 截图引用；外发每次都有净化报告与 digest。

FR-AUDIT-003 QA 违规记录
- 描述：伪执行宣告、Canon-as-fact、语言政策违规等必须进入 qa_violation_log。
- 验收：回归触发一次违规，可在日志面板检索并回放原文与处理结果。

### 4.8 外脑气闸（AIRLOCK）

FR-AIRLOCK-001 出站净化报告
- 描述：外发 payload 必须最小化并生成 sanitization_report；不得外发硬状态原始值/宪法全文/规则细节。
- 验收：每次外发都有 removed_fields/redactions/payload_digest；缺失则拒绝（AIRLOCK_BLOCKED_EGRESS）。

FR-AIRLOCK-002 入站隔离与分类
- 描述：外部返回内容必须分类（FACT/DRAFT_CODE/DRAFT_INSTRUCTION/UNKNOWN），不得直通执行。
- 验收：外部内容触发执行前必须被重构为 Intent 并通过 Governor；否则阻断（EXTERNAL_DATA_GATE）。

### 4.9 视觉熔断与 ClawdBot（VISION）

FR-VISION-001 视觉红线熔断
- 描述：支付/系统设置/删除/账号等红线场景，门禁不足必须拒绝（VISUAL_REDLINE）。
- 验收：红线场景除非满足更高门禁 + 二次确认，否则无法继续点击/输入。

FR-VISION-002 定位置信度门槛
- 描述：UI 元素定位 confidence < 0.80 必须停止并返回 UI_ELEMENT_NOT_FOUND。
- 验收：模拟低置信度定位，不发生任何点击；clawd_action_log 记录完整。

FR-VISION-003 屏幕监控与 OCR 触发门控（Gaming 不触发）
- 描述：屏幕监控与 OCR/识图仅在允许的 sys_mode 下触发；Gaming 模式必须禁止触发（含定时轮询与被动触发），不得对用户输出“我看到了屏幕内容”的无证据宣告。
- 验收：Gaming 模式下任意屏幕监控/OCR 请求均被拒绝或降级（带稳定 reason_code）；非 Gaming 模式触发时输出结构化结果（text_blocks/bbox/conf/source_ref）并可回放。

FR-VISION-004 摄像头识别事件化与证据引用
- 描述：摄像头识别必须以事件化结果输出（objects/events + confidence + evidence_ref），不得以自然语言替代证据引用；低置信度必须显式标注。
- 验收：任一摄像头识别事件可追溯到 evidence_ref；证据缺失时必须降级/拒绝并给出 reason_code。

FR-VISION-005 ClawdBot 必须依赖结构化视觉输入
- 描述：ClawdBot 的 UI 行为规划必须消费结构化视觉输入（OCR/检测结果与截图证据指针），不得直接凭空给出点击步骤；置信度不足必须熔断。
- 验收：缺结构化视觉输入时不得进入可执行 UI 行为路径；熔断可审计。

### 4.10 关系系统（BOND）

FR-BOND-001 Neo4j 作为 SSOT
- 描述：Bond 六维在 Neo4j，任何组件不得双写；SQLite 仅存 snapshot 派生缓存。
- 验收：除 bond_dynamics 外写 Neo4j 必失败；snapshot 写入可追溯 bond_update_log。

FR-BOND-002 Event 驱动更新
- 描述：Bond 变化只能由事件驱动，事件必须含 etype、intensity、evidence_refs。
- 验收：缺 evidence_refs 的 bond 更新被拒绝并记录 BOND_UPDATE_INVALID。

FR-BOND-003 给语义内核的 Bond Summary 降采样
- 描述：语义内核不得获得数值；只拿 stage/trend/capability_hint。
- 验收：输出出现“信任值=xx”等数值宣称，QA 必拦截。

### 4.11 记忆系统（MEMORY）

FR-MEM-001 Episodic 写入门禁与证据链
- 描述：写入必须有 evidence_refs；默认不写入“纯叙事输出”。
- 验收：无证据写入失败；日志可回放触发原因。

FR-MEM-002 衰减、合并、删除可审计
- 描述：条目有 decay_profile；合并保留证据链；删除需 intent+governor。
- 验收：合并后原条目保留 merged_into；删除能追溯原因与流程。
FR-MEM-003 文本嵌入服务（Embedding）必须存在且可降级
- 描述：必须提供可调用的 embedding 服务接口与健康状态；不可用时必须稳定降级并可观测，不得把“服务不可用”伪装成“记忆为空”的事实结论。
- 验收：embedding 不可用/超时时，输出结构稳定且 reason_code 可统计；审计链可回放。

FR-MEM-004 重排服务（Reranker）必须存在且可降级
- 描述：必须提供可调用的 reranker 服务接口与健康状态；不可用时必须稳定降级（例如退化为召回顺序或轻量排序），并显式标注降级阶段。
- 验收：reranker 不可用时，仍能输出 TopK evidence pack（字段稳定）且带 stage/reason_code。

FR-MEM-005 召回融合输出必须为“证据包”（evidence pack）
- 描述：Host 的召回融合必须输出结构化 evidence pack（候选、分数、摘要、来源指针、置信度），供后续回答与审计引用；不得仅输出自然语言“我找到了…”.
- 验收：同版本下 evidence pack 字段结构稳定；任何“完成性/事实性表述”必须能引用 evidence pack 或审计摘要字段。

### 4.12 风格骨架库（CANON）

FR-CANON-001 Canon 仅用于口吻，不得覆盖事实
- 描述：Canon 条目必须标记 tone_only/unsafe_as_fact；召回与注入字段隔离。
- 验收：Canon 被当事实引用时，QA 拦截并记 CANON_AS_FACT_VIOLATION。

FR-CANON-002 Host 主权与更新流程
- 描述：Canon SSOT 在 Host；更新必须走 Evolution/白名单 + 审计。
- 验收：任何解释器无法直接写 Canon；任何 Canon 更新都有提案与审计记录。

FR-WCANON-001 World Canon 最小权威内容集合（可引用、非执行事实）
- 描述：Host 侧维护 World Canon（系统术语、架构设定、关键不变量、必要世界观条目）；用于跨会话一致性引用与冲突裁决，但不构成执行事实。
- 验收：叙事引用 World Canon 时必须使用“设定/引用口径”；任何“已执行/已保存/已确认”仍必须引用审计链，否则 QA 拦截。

FR-WCANON-002 World Canon 版本化与更新可审计
- 描述：World Canon 更新必须走提案/白名单或 Evolution 流程，并在审计链中可回放（变更前后 hash/version 可追溯）。
- 验收：任意 World Canon 变更都能在 canon_change_log/canon_proposal_log（或语义等价日志）中定位到来源与证据引用。

FR-WCANON-003 注入分槽隔离（authoritative.world_canon）
- 描述：World Canon 必须以独立槽位注入 context_pack（与 style_pack/episodic/facts 分离），并携带 evidence_level=CANON_REFERENCE_ONLY。
- 验收：混槽注入视为缺陷；回归集中出现“Canon 当事实/当执行事实”时必须被拦截并记录违规事件。

### 4.13 sys_mode 与路由（MODE）

FR-MODE-001 三模式行为差异
- 描述：Normal/Gaming/Sleep 影响能力矩阵、输出节奏、资源预算。
- 验收：Gaming 禁止 clawd_ui_action；Sleep 禁止 CONTROL 类执行；拒绝码 SYSMODE_FORBIDDEN。

FR-MODE-002 来源可审计
- 描述：sys_mode 切换必须有来源证据（观测/用户授权/定时策略）。
- 验收：每次切换写 hard_state_log（原因、证据摘要），可回放。

FR-MODE-003 Gaming 模式视觉链路门控
- 描述：Gaming 模式下禁止屏幕监控/OCR；同时禁止任何依赖屏幕帧的 ClawdBot UI 自动化链路；仅允许低风险对话与语音外显。
- 验收：进入 Gaming 后，屏幕监控/OCR/Clawd UI 动作均被稳定拒绝或降级；退出 Gaming 后按规则恢复且审计可回放。

### 4.14 LSP（本地沙盒剖面）（LSP）

FR-LSP-001 Artifact Vault 可寻址可 GC
- 描述：截图/关键帧/回滚材料进入 Vault，具 hash 与 policy_profile_id；GC 可审计且不得删有效 undo 窗口内材料。
- 验收：GC 后仍可证明未删有效回滚材料；vault_gc_log 完整。

FR-LSP-002 manifest 作为回滚清单
- 描述：可逆变更产出 manifest（before/after/path/hash/refs/rollback_steps/digest）。
- 验收：manifest digest 不匹配时，Undo 必拒绝。

### 4.15 交互与故障处理（UX）

FR-UX-001 阶段化状态机与降级
- 描述：S0..S8 每阶段有失败分支与 reason_code；降级最多追问 1 个关键问题。
- 验收：注入任意阶段异常，输出不崩；quality.stage 反映失败点；提示克制一致。

FR-UX-002 Host 最小叙事模板库
- 描述：LLM 不可用/不合规时，Host 输出最低风格且不伪执行的模板（不暴露内部组件名）。
- 验收：关闭 LLM 或强制超时，仍输出合规模板；不产生执行 intent。

### 4.16 可观测与运维（OBS）

FR-OBS-001 关键指标与面板
- 描述：展示拒绝原因分布、伪执行命中次数、召回概览、Hard Snapshot、最近链路摘要。
- 验收：本地运行可一键查看；日志字段对齐 request_id/intent_id。

FR-OBS-002 开发/生产模式差异
- 描述：开发模式可显示内部 debug trace；生产模式默认隐藏，仅留 user_facing_hint。
- 验收：切换模式后输出差异符合预期；不影响裁决与审计事实。

FR-OBS-003 监控面板 v3 前端架构（多页面 SPA + Dify 简约风格）
- 描述：将现有单页 panel.html 重建为多页面 SPA 架构，采用 Dify 风格简约设计系统（白色/浅灰背景、蓝色主色调、卡片式布局、左侧导航栏）；CSS/JS 完全分离，支持页面路由切换（Overview / Sessions / Trace / Metrics）。
- 关键约束：
  - 纯原生 HTML/CSS/JS（无构建工具依赖），可直接由 FastAPI StaticFiles 托管；
  - 引入 Bootstrap Icons 作为图标库；
  - 左侧固定导航栏，顶部面包屑，主内容区自适应；
  - 响应式布局（≥1024px 双栏，<1024px 单栏）。
- 验收：
  - 四个页面均可正常渲染且无 JS 报错；
  - 导航切换流畅，URL hash 路由可书签；
  - 在 1280×800 和 1920×1080 分辨率下布局正常。

FR-OBS-004 全服务健康总览页（Overview）
- 描述：展示双端（Host + Edge）所有服务的实时健康状态，包括：ASR、TTS、VTS/Live2D、LLM Gateway、Neo4j、SQLite Ledger、Chroma/向量库、Scheduler、Bond Engine、Memory Distiller、MCP Provider 等；每个服务以卡片形式展示状态（UP/DOWN/DEGRADED）、响应延迟、版本号、最后检查时间。
- 关键约束：
  - 30 秒自动轮询，支持手动刷新；
  - 服务状态变化时卡片颜色/图标即时更新；
  - 顶部汇总行：总服务数 / 健康数 / 降级数 / 故障数。
- 验收：
  - 所有已注册服务均出现在总览页；
  - DOWN 状态服务卡片显示红色边框 + 错误摘要；
  - 轮询失败时显示"上次已知状态"而非崩溃。

FR-OBS-005 会话列表页（Sessions）
- 描述：展示历史会话列表，支持按时间范围、状态（OK/REJECT/ERROR）、关键词搜索过滤；每行显示 request_id、时间戳、状态、耗时、来源通道；支持分页（每页 20 条）。
- 关键约束：
  - 点击任意会话行跳转到链路可视化页（Trace 页）并自动加载该 request_id；
  - 支持 URL 参数传递 request_id（?rid=xxx）；
  - 列表支持按时间倒序排列。
- 验收：
  - 列表正常分页，翻页不丢失过滤条件；
  - 点击跳转后 Trace 页自动展示对应链路；
  - 搜索关键词高亮显示。

FR-OBS-006 Dify 风格链路可视化页（Trace）
- 描述：以节点流图方式展示单次请求的完整链路，参考 Dify Workflow 编排视图风格（只读展示，不可编辑）：
  - 每个节点为圆角卡片，显示节点名称（Intent / Governor / Skill / ASR / TTS / VTS 等）、状态图标、耗时；
  - 节点间用有向连线连接，表示执行顺序；
  - 点击节点展开侧边面板，显示该节点的入参（input）、出参（output）、状态、reason_code、时间戳；
  - 支持节点状态颜色区分：成功（绿）/ 拒绝（红）/ 降级（橙）/ 运行中（蓝动画）。
- 关键约束：
  - 节点布局自动计算（从左到右，支持分支/并行节点）；
  - 入参/出参以 JSON 格式化展示，支持折叠/展开；
  - 支持全屏模式；
  - 节点数量 ≤50 时渲染流畅（无卡顿）。
- 验收：
  - 给定 request_id 后节点图正确渲染；
  - 点击节点侧边面板正确显示入参/出参；
  - 并行节点（如 ASR+TTS 同时）以分支形式展示；
  - 空链路时显示友好空状态。

FR-OBS-007 指标大盘页（Metrics）
- 描述：展示系统级指标趋势，包括：请求量趋势（折线图）、拒绝率趋势（折线图）、reason_code 分布（横向条形图）、各服务响应延迟分布（P50/P95 柱状图）、Edge 服务资源使用趋势（ASR/TTS 队列深度）。
- 关键约束：
  - 时间范围选择器：最近 1h / 6h / 24h / 7d；
  - 图表使用 Canvas 原生绘制（无第三方图表库依赖）；
  - 数据点不足时显示"数据采集中"占位。
- 验收：
  - 切换时间范围后图表数据正确更新；
  - 图表在 HiDPI 屏幕下清晰（devicePixelRatio 支持）；
  - 无数据时不崩溃，显示友好提示。

FR-OBS-008 全服务健康聚合 API
- 描述：提供 GET /obs/services/health 端点，聚合返回双端所有已注册服务的健康状态快照，包含：service_id、name、side（host/edge）、status（UP/DOWN/DEGRADED）、latency_ms、version、last_check_ts、error_summary（可选）。
- 关键约束：
  - 响应时间 ≤500ms（并发探测，非串行）；
  - 单个服务探测失败不影响其他服务返回；
  - 支持 ?side=host|edge 过滤参数。
- 验收：
  - 所有已注册服务均出现在响应中；
  - 单服务超时时该服务 status=DOWN，其余正常返回；
  - 响应结构稳定（字段不随服务状态变化而缺失）。

FR-OBS-009 会话列表 API
- 描述：提供 GET /obs/sessions 端点，返回分页会话列表，支持过滤参数：start_time、end_time、status（OK/REJECT/ERROR）、channel（voice/wechat/api）、keyword（模糊匹配 request_id/intent）；每条记录包含：request_id、session_id、ts_ms、status、duration_ms、channel、reason_code（若拒绝）。
- 关键约束：
  - 分页参数：page（默认1）、page_size（默认20，最大100）；
  - 响应包含 total_count 用于前端分页渲染；
  - 查询性能：10 万条记录下 p95 ≤200ms。
- 验收：
  - 过滤参数组合正确生效；
  - total_count 与实际数据一致；
  - 空结果时返回 results=[] 而非 404。

FR-OBS-010 链路节点详情 API
- 描述：提供 GET /obs/trace/{request_id} 端点，返回指定请求的结构化节点列表，每个节点包含：node_id、node_type（intent/governor/skill/asr/tts/vts/llm/memory/bond 等）、node_name、status、start_ts_ms、end_ts_ms、duration_ms、input（JSON）、output（JSON）、reason_code（可选）、parent_node_id（支持树形结构）、side（host/edge）。
- 关键约束：
  - 节点顺序按 start_ts_ms 排序；
  - input/output 字段大小限制 ≤64KB（超出截断并标注 truncated=true）；
  - 支持 ?include_io=false 参数跳过入参/出参（用于列表预览）。
- 验收：
  - 给定有效 request_id 返回完整节点列表；
  - 节点 parent_node_id 正确反映执行树结构；
  - 不存在的 request_id 返回 404 + 稳定错误结构。

FR-OBS-011 指标时序 API
- 描述：提供 GET /obs/metrics/timeseries 端点，返回多维度时序数据，支持参数：metric（request_count/reject_rate/latency_p50/latency_p95/reason_code_dist）、interval（1m/5m/1h）、start_time、end_time；响应格式：{metric, interval, data_points: [{ts, value}], unit}。
- 关键约束：
  - 数据点数量上限 1000（超出自动降采样）；
  - 无数据区间返回 null 值点（不跳过），保证时序连续性；
  - 支持多 metric 批量查询（?metric=request_count,reject_rate）。
- 验收：
  - 时序数据点时间间隔与 interval 参数一致；
  - 降采样后数据点数量 ≤1000；
  - 批量查询返回 {metrics: {metric_name: {data_points: [...]}}} 结构。

### 4.17 通道与外显（CHANNEL/AVATAR）

FR-CHANNEL-001 微信通道 I/O 事件化与审计串联
- 描述：微信收发必须被建模为确定性的通道事件（inbound/outbound），并写入可追溯审计链；LLM 不得自行决定“是否发送/发给谁/发什么”。
- 验收：任一出站消息都能在审计链中追溯到 thread_key 与 request_id；失败必须可解释（reason_code + stage），且可回放到同一 intent_id。

FR-AVATAR-001 外显驱动确定性映射（VTS）
- 描述：外显（VTube Studio 等）只能使用固定枚举 emotion_tag→expression_id 的确定性映射；拒绝模型提交自由动作参数或未知 tag。
- 验收：非法 emotion_tag/自由动作参数被拒绝并产生可统计 reason_code；合法触发可回放到固定映射表（同版本同输入输出一致）。

### 4.18 MCP Providers 接入（MCP）

FR-MCP-001 MCP provider 元数据同步 + 受治理调用（READ/WRITE 分级）
- 描述：MCP provider 暴露的工具必须先同步为 ToolMeta（仅元数据），并按 READ/WRITE 分级纳入治理；所有 MCP 调用必须经 ToolBroker 单入口并串联审计字段（provider_id/tool_id/request_id/evidence_ref）。
- 验收：MCP WRITE 在无 token 或未 APPROVE 时必拒绝且可统计；所有 MCP 调用都可追溯到 intent/governor/skill_exec 记录，并满足 AIRLOCK 出站最小化要求。

### 4.19 语音链路（VOICE）

FR-VOICE-001 ASR 事件化上送（Edge→Host）
- 描述：Edge ASR（large-v3）必须上送事件化转写（流式或分段），包含时间戳与置信度；可选 alternates（N-best）用于检索提示。
- 验收：转写事件结构稳定；低置信度显式标注；可回放。

FR-VOICE-002 TTS 合成与播报队列（Host→Edge）
- 描述：Edge TTS（GPT-SoVITS，全 GPU 常驻）必须提供“合成+播报队列”统一能力；Host 只下发最终口径文本与情绪标签。
- 验收：播报事件可关联 request_id；失败可解释（reason_code/stage）；不出现伪执行宣告。

FR-VOICE-003 强交互可打断（Barge-in）
- 描述：用户开口时可打断当前播报，优先保证 ASR 继续上送；打断不构成任何执行事实或写入事实。
- 验收：打断立即生效；审计记录打断事件；ASR 不被阻塞。

FR-VOICE-004 Edge 兜底小模型（Ollama 3B 量化）默认卸载、低频触发
- 描述：兜底小模型作为草案能力（在场回应/输入清洗/摘要压缩/检索提示），默认卸载、按需加载；不可用时快速失败并回退到 Host 兜底口径。
- 验收：任何兜底输出均标记为 draft-only；不得包含裁决或执行事实宣告；不可用时 reason_code 可统计。

FR-VOICE-005 Edge 资源闸门（优先级与互斥）
- 描述：Edge 必须实施资源闸门，避免 ASR/TTS/兜底LLM 峰值叠加导致不稳定；优先级：ASR > 播放 > TTS 合成 > 兜底 LLM。
- 验收：压力下仍保证 ASR；资源不足时降级行为可观测且可回放。

### 4.20 定时调度器（SCHEDULER）

FR-SCHED-001 Host 定时调度器（Scheduler）统一入口
- 描述：Host 必须提供一个可配置的定时调度器，支持注册周期性或定时触发的"系统发起 Intent"；所有系统发起的 Intent 仍必须走 Governor→Token→Skill→Audit 单向链，与用户发起的请求享有完全相同的治理约束。
- 关键约束：
  - 调度器只负责"何时触发"与"触发何种 intent_draft"，不负责裁决或执行；
  - 每次触发必须生成 system_initiated_intent（proposer_node_id 标注为 scheduler）；
  - Gaming/Sleep 模式下调度器行为受 sys_mode 门控（Gaming 禁止低优先级任务，Sleep 仅允许白名单任务）；
  - 调度器配置必须版本化、可审计（变更写 scheduler_config_log）。
- 验收：
  - 调度器注册/注销任务可回放；
  - sys_mode=GAMING 时低优先级任务被拦截（SYSMODE_FORBIDDEN）；
  - 所有调度触发的 Intent 在审计链中可追溯到 scheduler 来源。

FR-SCHED-002 调度任务健康状态与降级
- 描述：调度器必须维护任务健康状态（last_run_ts/next_run_ts/status/error_count），任务连续失败必须自动降级（暂停 + 告警）而非无限重试。
- 验收：
  - 连续失败 N 次后自动暂停并记录 SCHED_TASK_SUSPENDED；
  - 健康快照可被 /health 端点或观测面板读取。

### 4.21 桌面留言（DESKTOP）

FR-DESKTOP-001 桌面留言文件创建与撤销
- 描述：Natsume 可在 Master 桌面（或指定安全目录）创建/更新文本留言文件；操作默认可逆（reversible=true），必须产出 manifest + undo_token。
- 关键约束：
  - 写入路径限制在安全基目录（base_dir allowlist），禁止路径穿越（`..`/符号链接）；
  - risk_level=2，clearance_level=L1，scope=filesystem；
  - 不需要 confirm_token（低风险），但必须走 Governor 审批；
  - 删除默认 move-to-quarantine（不真删），真删需 confirm_token。
- 验收：
  - 正向：留言文件创建成功，manifest 包含 before(空)/after/path/hash；
  - 撤销：undo_token 调用后文件恢复原状态；
  - 拒绝：路径越界 → SCOPE_VIOLATION；Gaming 模式 → SYSMODE_FORBIDDEN；
  - 降级：文件系统不可写 → SKILL_EXEC_FAILED + reason_code 可解释。

FR-DESKTOP-002 桌面留言内容约束
- 描述：留言内容由语义内核生成草案，Host QA 检查后写入；内容不得包含执行事实宣告（仅允许提醒/情感/备忘口径）。
- 验收：内容含伪执行句时 QA 拦截并重写；写入审计可追溯原始草案与最终内容。

### 4.22 梦境（DREAM）

FR-DREAM-001 梦境生成（定时触发 + Episodic 回顾）
- 描述：Natsume 在 Sleep 模式或空闲时段，由调度器触发"梦境生成"：读取当日 Episodic Memory → 语义内核生成创造性梦境叙事 → 写入 Episodic（带 dream 标签）。
- 关键约束：
  - 触发条件：sys_mode=SLEEP 或 idle_duration ≥ 阈值（可配置）；
  - risk_level=1，clearance_level=L0，scope=memory，reversible=true；
  - 梦境内容标记为 category=dream + evidence_level=NARRATIVE_ONLY（非执行事实）；
  - LLM 生成的梦境叙事仍受 QA 约束（不得伪执行/Canon-as-fact）。
- 验收：
  - 正向：梦境写入 Episodic 并可按 dream 标签召回；
  - 门禁：Normal 模式无空闲时拒绝（SYSMODE_FORBIDDEN 或 SCHED_CONDITION_UNMET）；
  - 降级：Episodic 不可用时降级但不伪造（DEPENDENCY_UNAVAILABLE）。

FR-DREAM-002 梦境内容可回溯但不可当事实
- 描述：梦境内容在后续对话中可被召回引用，但必须以"梦境/想象"口径呈现，不得升级为执行事实或记忆事实。
- 验收：召回梦境内容时 QA 确认 evidence_level=NARRATIVE_ONLY 标注存在；缺失则拦截。

### 4.23 日记（DIARY）

FR-DIARY-001 日记生成（每日定时汇总）
- 描述：Natsume 每日定时（可配置时间点）对当日对话进行自我回顾与反思，生成结构化日记条目并写入本地文件 + Episodic Memory。
- 关键约束：
  - 触发条件：每日固定时间（如 23:00）由调度器触发；
  - risk_level=1，clearance_level=L0，scope=memory+filesystem，reversible=true；
  - 日记写入路径限制在安全基目录（与桌面留言共享 base_dir allowlist）；
  - 日记内容标记为 category=diary；
  - 日记产出 manifest（文件 before/after）+ memory evidence_refs。
- 验收：
  - 正向：日记文件创建成功，Episodic 条目写入成功，审计链完整；
  - 门禁：无当日对话时输出"无内容"而非伪造日记；
  - 降级：文件系统或 Episodic 不可用时降级且不伪造。

FR-DIARY-002 日记与 Evolution 提案关联（可选扩展）
- 描述：日记中若产生"设定漂移建议"，可生成 canon_proposal 进入 Evolution 审核流程（白皮书概念）。此为可选扩展，不阻塞基本日记功能。
- 验收：提案写入 canon_proposal_log 且可追溯到日记 intent_id。

### 4.24 主动对话（PROACTIVE）

FR-PROACTIVE-001 主动对话触发策略
- 描述：Natsume 在无用户输入时，可基于条件策略（空闲时长、Bond 状态、时间段、上下文残留）主动发起对话；主动对话仍必须走完整单向链（system_initiated_intent → Governor → Skill → Audit）。
- 关键约束：
  - 触发条件组合：idle_duration ≥ 阈值 AND bond.stage 满足条件 AND sys_mode 允许；
  - risk_level=1，clearance_level=L0，scope=dialogue；
  - 频率限制：每小时最多 N 次（可配置），防止骚扰；
  - Gaming 模式禁止主动对话；Sleep 模式仅允许低频问候。
- 验收：
  - 正向：空闲触发后用户端收到主动消息（文本/语音），审计链完整；
  - 门禁：Gaming → SYSMODE_FORBIDDEN；频率超限 → RATE_LIMIT_EXCEEDED；
  - 降级：Edge 不可达时仅记录 intent 不推送（DEPENDENCY_UNAVAILABLE）。

FR-PROACTIVE-002 主动对话内容约束与推送通道
- 描述：主动对话内容由语义内核生成草案，Host QA 检查后通过 Edge RPC（语音/文本）或微信通道推送；内容不得包含执行请求或事实宣告。
- 验收：
  - 内容仅限问候/关心/回忆引用/提醒口径；含执行 intent 时 QA 拦截；
  - 推送通道失败时静默降级且不重试推送（防骚扰）。

### 4.26 LLM 网关真实接入（LLM_GW）

FR-LLM-GW-001 Cloud LLM Gateway 真实 API Key 接入与健康检测
- 描述：Cloud LLM Gateway 必须支持真实 API Key 配置（OpenAI/Anthropic/本地 Ollama 等），并在启动时与心跳任务中探测可用性；不可用时必须稳定降级（DEPENDENCY_UNAVAILABLE）而非崩溃。
- 关键约束：
  - API Key 不得硬编码，必须从环境变量或加密配置文件读取；
  - 健康探测结果必须写入 /health 端点的 runtime_statuses（llm_gateway.status）；
  - 不可用时输出结构稳定，quality.stage 标注 LLM_UNAVAILABLE；
  - 不得把"LLM 不可用"伪装成"我没有任何想法"的事实结论。
- 验收：
  - 配置有效 API Key 时，LLM 调用成功并写入 llm_call_log；
  - API Key 缺失/无效时，输出结构稳定，reason_code=LLM_UNAVAILABLE；
  - 心跳任务探测 LLM 可用性，结果写入 HEARTBEAT_OK/HEARTBEAT_ALERT 审计事件；
  - 对应测试编号：T-LLM-GW-01~05。

FR-LLM-GW-002 AIRLOCK 出站净化完整链路
- 描述：所有发往 Cloud LLM 的 payload 必须经 AIRLOCK 出站净化（sanitization_report），移除硬状态原值/宪法全文/规则细节；入站返回内容必须经 AIRLOCK 入站隔离分类（FACT/DRAFT_CODE/DRAFT_INSTRUCTION/UNKNOWN）。
- 关键约束：
  - 出站净化报告必须包含 removed_fields/redactions/payload_digest；
  - 入站内容不得直通执行，必须重构为 Intent 并通过 Governor；
  - 净化失败必须拒绝出站（AIRLOCK_BLOCKED_EGRESS）。
- 验收：
  - 每次 LLM 调用都有 sanitization_report 可追溯；
  - 入站内容触发执行前必须经 Governor 裁决；
  - 对应测试编号：T-LLM-GW-06~10。

FR-LLM-GW-003 多模型路由与降级策略
- 描述：LLM Gateway 必须支持多模型路由（主模型/备用模型/本地兜底），按优先级降级；每次路由决策必须写入 llm_route_log（model_id/reason/latency_ms）。
- 关键约束：
  - 主模型超时/不可用时自动切换备用模型；
  - 本地兜底（Ollama）作为最后防线，输出标记 draft_only=true；
  - 路由策略可配置（config.yaml），变更写 llm_route_config_log。
- 验收：
  - 主模型超时时自动切换备用，llm_route_log 记录切换原因；
  - 所有模型不可用时输出稳定降级结构；
  - 对应测试编号：T-LLM-GW-11~15。

### 4.27 VTS 表情驱动完整链路（VTS_DRIVE）

FR-VTS-DRIVE-001 emotion_tag→expression_id 映射表持久化与版本化
- 描述：VTS 表情映射表（emotion_tag→expression_id）必须持久化存储（SQLite 或 JSON 文件），支持版本化管理；映射表变更必须写入 vts_mapping_change_log（变更前后 hash/version）。
- 关键约束：
  - 映射表必须有唯一版本号（semver 或 hash）；
  - 非法 emotion_tag 必须拒绝（AVATAR_INVALID_EMOTION_TAG）；
  - 映射表加载失败时必须降级（DEPENDENCY_UNAVAILABLE），不得使用硬编码默认值。
- 验收：
  - 映射表变更后版本号更新，vts_mapping_change_log 可追溯；
  - 非法 emotion_tag 被拒绝并产生可统计 reason_code；
  - 对应测试编号：T-VTS-DRIVE-01~05。

FR-VTS-DRIVE-002 VTS RPC 真实调用链路与审计串联
- 描述：VTS 表情触发必须通过 Edge RPC 真实调用 VTube Studio API，并将调用结果（expression_id/status/latency_ms）写入 vts_action_log，串联 request_id/intent_id。
- 关键约束：
  - VTS RPC 调用失败必须降级（EDGE_RPC_FAILED），不得伪造"表情已触发"；
  - 每次触发必须有 evidence_ref 指向 vts_action_log；
  - VTS 不可用时输出结构稳定，reason_code 可统计。
- 验收：
  - 正向：VTS 调用成功，vts_action_log 记录完整（expression_id/status/latency_ms/request_id）；
  - 失败：VTS 不可用时降级，reason_code=EDGE_RPC_FAILED，不伪造；
  - 对应测试编号：T-VTS-DRIVE-06~10。

FR-VTS-DRIVE-003 表情触发频率限制与队列管理
- 描述：VTS 表情触发必须有频率限制（每秒最多 N 次，可配置），防止 VTS API 过载；超限时排队或丢弃（可配置策略），并写入 vts_rate_limit_log。
- 关键约束：
  - 频率限制配置可热更新（不重启）；
  - 丢弃策略：FIFO 丢弃最旧请求（默认）或拒绝最新请求（可配置）；
  - 频率超限时 reason_code=VTS_RATE_LIMIT_EXCEEDED。
- 验收：
  - 超限时触发丢弃/拒绝，vts_rate_limit_log 记录；
  - 频率配置热更新后立即生效；
  - 对应测试编号：T-VTS-DRIVE-11~14。

### 4.28 微信通道完整链路（WECHAT）

FR-WECHAT-001 微信 inbound 事件解析与审计串联
- 描述：微信 inbound 消息（文本/图片/语音/事件）必须被解析为标准化 WeChatInboundEvent，并写入 wechat_inbound_log（thread_key/msg_type/content_hash/ts_ms/request_id）；解析失败必须降级（WECHAT_PARSE_FAILED）。
- 关键约束：
  - 消息内容不得直通 LLM，必须先经 AIRLOCK 入站隔离分类；
  - thread_key 必须与 request_id 关联，支持跨会话追溯；
  - 图片/语音消息必须先经 OCR/ASR 转写，再进入 Intent 流程。
- 验收：
  - 文本消息：解析成功，wechat_inbound_log 记录完整；
  - 图片消息：OCR 转写后进入 Intent 流程，evidence_ref 可追溯；
  - 解析失败：reason_code=WECHAT_PARSE_FAILED，结构稳定；
  - 对应测试编号：T-WECHAT-01~06。

FR-WECHAT-002 微信 outbound 推送与频率限制
- 描述：微信 outbound 消息必须经 AIRLOCK 出站净化后推送，并写入 wechat_outbound_log（thread_key/msg_type/content_hash/ts_ms/request_id/status）；频率限制：每个 thread_key 每分钟最多 N 条（可配置）。
- 关键约束：
  - 推送失败必须降级（WECHAT_SEND_FAILED），不得伪造"已发送"；
  - 频率超限时拒绝推送（RATE_LIMIT_EXCEEDED），不排队（防骚扰）；
  - 出站内容不得包含硬状态原值/规则细节（AIRLOCK 约束）。
- 验收：
  - 正向：推送成功，wechat_outbound_log 记录完整；
  - 频率超限：拒绝推送，reason_code=RATE_LIMIT_EXCEEDED；
  - 推送失败：降级，reason_code=WECHAT_SEND_FAILED，不伪造；
  - 对应测试编号：T-WECHAT-07~12。

FR-WECHAT-003 微信通道健康状态与降级策略
- 描述：微信通道必须维护健康状态（last_recv_ts/last_send_ts/status/error_count），并暴露到 /health 端点（wechat_channel.status）；连续失败必须自动降级（WECHAT_CHANNEL_DEGRADED）。
- 关键约束：
  - 健康状态 30 秒自动刷新；
  - 降级时仍接受 inbound（只停止 outbound 推送）；
  - 恢复时自动重置降级状态并写 wechat_channel_recovery_log。
- 验收：
  - 连续失败 N 次后状态变为 DEGRADED，/health 端点反映；
  - 降级期间 inbound 正常处理，outbound 静默丢弃；
  - 对应测试编号：T-WECHAT-13~16。
- 描述：Focus Mode 为 sys_mode 的子状态扩展（Normal 的子模式），进入后限制中断型能力（主动对话/通知推送静默），保留被动响应能力（用户主动提问仍可回复）。
- 关键约束：
  - 进入/退出必须有来源证据（用户指令/定时策略/日历事件），并写 hard_state_log；
  - Focus Mode 下 Scheduler 的 proactive/dream 等非紧急任务暂停；
  - 对话能力不受影响（用户输入仍正常处理）。
- 验收：
  - 进入 Focus 后主动对话被拦截（SYSMODE_FORBIDDEN）；
  - 用户主动提问正常响应；
  - 退出 Focus 后调度器恢复；审计可回放切换事件。

---

## 5. 非功能需求（NFR）

NFR-SEC-001 本地优先与最小暴露
- 要求：默认本地运行；对外通信必须经 AIRLOCK；敏感字段不得出站。
- 验收：出站净化报告覆盖率=100%；抽检出站 payload 不含硬状态原值与规则细节。

NFR-REL-001 降级可用性
- 要求：依赖故障不崩溃；输出结构稳定；关键链路可观测。
- 验收：故障注入矩阵全通过（DB/Neo4j/Chroma/Cloud Exocortex/Edge 超时或不可用）。

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
- 配置资产也必须可追溯：包括但不限于 Flow 资产（LangGraph 图/配置）及其环境指针；它们必须能映射到具体需求编号与验收用例。
- docs/60_trace/ 下应存在“索引文件”指向当前生效 Flow（例如 docs/60_trace/flows/index.md），以避免“仓库里有很多配置但不知道用哪一个”。

说明：
- 映射表由 `docs/60_trace/requirements_trace.md`（或等价）维护；PRD 只定义规则与编号体系。
- Trace 既追溯“代码实现”，也追溯“可部署配置资产”；当行为变化来自 Flow，而非 Host 代码时，仍必须有 ReqID 与验收记录可引用。

---

## 7. 里程碑（按“可验收能力包”交付）

M1 主权闭环（最小可运行）：FR-CORE/FLOW/INTENT/GOV/TOKEN/SKILL/AUDIT（基础）— **DONE**（2026-03-01）
M2 成长闭环（关系+记忆+风格）：FR-BOND/MEM/CANON — **DONE**（2026-03-01）
M3 LSP（可撤销默认）：FR-LSP + 可逆能力接入 — **DONE**（2026-03-02）
M4 体验包（低风险）：FR-SCHED（调度器前置）+ FR-DESKTOP（桌面留言）/ FR-DREAM（梦境）/ FR-DIARY（日记）/ FR-PROACTIVE（主动对话）/ FR-FOCUS（Focus Mode）（均需 SkillMeta + 审计）— **DONE**（2026-03-03）
M5 视觉自动化（高门槛）：FR-VISION 全量验收 — **DONE**（2026-03-02）
M6 Edge 深化：语音/头像/微信红线治理（作为受治理 Skills）— **DONE**（2026-03-02）
M7 可观测 + MCP + 语音深化：FR-OBS-001/002（指标面板与模式差异）+ FR-MCP-001（MCP provider 治理）+ FR-VOICE-001~005（ASR/TTS/Barge-in/兜底模型/资源闸门）— **DONE**（2026-03-03：914 passed，OBS/MCP/VOICE 全量实现）
M8 运行时集成闭环：NFR-PERF-001（性能基线采集）+ NFR-REL-001（故障注入矩阵全通过）+ FR-CORE-004（冷启动端到端可验收）+ 运行时脚本集成（OBS/MCP 新模块接入 start_host_side.ps1）— **DONE**（2026-03-03：980 passed，性能基线/故障注入/冷启动/脚本集成全量实现）
M9 World Canon 闭环 + 日记 Evolution 关联：FR-WCANON-001~003（World Canon 文件 SSOT + 索引同步 + 注入分槽 + Evolution 提案流程）+ FR-DIARY-002（日记→canon_proposal 关联）— **DONE**（2026-03-03：1032 passed，World Canon SSOT/同步/分槽/Evolution/日记关联全量实现；R-85~R-89 追踪完成；m9_release.json 已生成）
M10-A Canon 治理 API：FR-WCANON-002 扩展（Canon 提案审核 REST API：/canon/proposals CRUD + approve/reject 端点 + 审计串联）— **DONE**（2026-03-03：1041 passed，canon_routes.py 7 个端点全量实现；T-Canon-API-01~08 全部通过；R-90~R-91 追踪完成）
M10-B ASR 热词注入：FR-ASR-001（WorldCanonStore.extract_hotwords + GET /canon/hotwords + inject_canon_hotwords）— **DONE**（2026-03-03：1054 passed；R-92~R-94 追踪完成）
M10-C 记忆蒸馏系统：FR-MEM-001~003（MemoryDistillationEngine.distill_top_k + semantic_memory 表 + context_pack.semantic_slot）— **DONE**（2026-03-03：1062 passed；R-95~R-97 追踪完成）
M10-D P1/P2 收口：F-41/F-06/F-35/F-36/F-37/F-39/F-12/F-05/F-01/F-02/F-11 全部修复 + Edge 端优化（TTS/VTS/ASR 全链路贯通）— **DONE**（2026-03-04：1090 passed；R-98~R-108 追踪完成；m10d_release.json 已生成）
M10-E _CANON_HOTWORDS_CACHE TTL 自动失效：FR-ASR-001 + NFR-PERF-001（_HotwordsCache 类 TTL=300s + T-HW-TTL-01~05）— **DONE**（2026-03-04：1090 passed；R-109 追踪完成；m10e_release.json 已生成）
M10-P2 P2 代码质量收口：F-16 _fetch_by_request 白名单校验补全 + F-13/F-14/F-15/F-25/F-38 已修复核查 + F-04/F-07/F-08 设备验证包标记 BLOCKED_ENV — **DONE**（2026-03-04：1090 passed；R-110~R-112 追踪完成）
M11 调度器 + 桌面留言 + 梦境 + 日记 + 主动对话 + Focus Mode：FR-SCHED-001/002 + FR-DESKTOP-001/002 + FR-DREAM-001/002 + FR-DIARY-001 + FR-PROACTIVE-001/002 + FR-FOCUS-001 — **DONE**（2026-03-04：1164 passed）
M11-A 定时调度器：FR-SCHED-001/002（DevSpec §21 工程契约 + NatsumeScheduler + SchedulerTaskRegistry + SchedulerHealthReporter + REST API 8 端点 + T-SCHED-01~07）— **DONE**（2026-03-04：1102 passed；R-113~R-115 追踪完成）
M11-B 桌面留言：FR-DESKTOP-001/002（DevSpec §22 工程契约 + desktop_note.py 已实现；T-DESK-01~08 单元测试全部通过）— **DONE**（2026-03-04）
M11-C 梦境：FR-DREAM-001/002（DevSpec §23.1 工程契约 + dream_generator.py 已实现；T-DREAM-01~05 单元测试全部通过）— **DONE**（2026-03-04）
M11-D 日记：FR-DIARY-001/002（DevSpec §23.2 工程契约 + diary_writer.py 已实现；T-DIARY-01~05 单元测试全部通过）— **DONE**（2026-03-04）
M11-E 主动对话：FR-PROACTIVE-001/002（DevSpec §23.3 工程契约 + proactive_talk.py 已实现；T-PROACT-01~06 单元测试全部通过）— **DONE**（2026-03-04）
M11-F Focus Mode：FR-FOCUS-001（DevSpec §23.4 工程契约 + focus_mode.py 已实现；T-FOCUS-01~04 单元测试全部通过）— **DONE**（2026-03-04）

M12 ToolBroker + 上下文压缩 + Reranker 强化：
M12-A ToolBroker 单入口 + tool_visibility_hash：FR-TOOL-001/002/003（ToolBroker 单入口 + WRITE Token gating + tool_visibility_hash 审计串联）— **DONE**（2026-03-04）
M12-B 多轮对话上下文压缩：FR-MEM-005 扩展（ContextCompressor：sliding_window/summary/hybrid + EvidencePack 构建）— **DONE**（2026-03-04）
M12-C Reranker 服务强化：FR-MEM-004（RerankerServiceContract 健康状态 + 降级策略 + EvidencePack stage 字段）— **DONE**（2026-03-04）
M12-D 技能热加载：FR-SKILL-001/002 扩展（SkillHotLoader + SkillVersionRegistry：热加载/回滚/一致性检查/审计）— **DONE**（2026-03-04）

M13 Bond 阶段自动晋级 + 长期记忆蒸馏强化：
M13-A Bond 阶段积分 + 自动晋级：FR-BOND-001/002/003 扩展（BondStageEngine：积分累积/阶段晋级/降级/趋势/审计串联 + Bond REST API：/bond/stage /bond/summary /bond/event /bond/users /bond/health）— **DONE**（2026-03-04）
M13-B 长期记忆蒸馏强化：FR-MEM-DISTILL-001/002（LongTermMemoryDistiller：EpisodicMemory→语义记忆蒸馏→Canon pending 提案 + 调度器 CRON 任务工厂 03:00）— **DONE**（2026-03-04）
M13-C Bond 事件端到端集成：FR-BOND-001/002（BondDynamicsWriter.apply_update 接入 BondStageEngine.apply_event；可选注入；stage_transition 字段）— **DONE**（2026-03-04）
M13-D M13 发布清单：m13_release.json（4 核心文件 hash；102 M13 测试通过；1342 全量通过；release_ready=true）— **DONE**（2026-03-04）

**完成的需求编号清单**：FR-BOND-001、FR-BOND-002、FR-BOND-003、FR-MEM-DISTILL-001、FR-MEM-DISTILL-002（R-121~R-130）

**回归测试清单**：
- T-BOND-STAGE-01~09（BondStageEngine：积分累积/晋级/降级/趋势/审计/边界）→ 30 passed
- T-BOND-API-01~08（Bond REST API：/bond/stage /bond/summary /bond/event /bond/users /bond/health）→ 18 passed
- T-DISTILL-B-01~10（LongTermMemoryDistiller：蒸馏/幂等/提案上限/CRON 配置）→ 36 passed
- T-BOND-INTEG-01~08（BondDynamicsWriter + BondStageEngine 端到端联动；向后兼容；异常非致命）→ 18 passed
- 全量回归：1342 passed，2 skipped，0 failed

**风险回顾**：
- 新增 scope：Bond 阶段引擎（bond_stage_engine.py）+ 长期记忆蒸馏（long_term_distiller.py）均为新模块，不修改现有 SSOT 写路径，风险低
- 红线：蒸馏结果只生成 pending 提案，不直接写 Canon SSOT（FR-MEM-DISTILL-001 约束保持）；Bond 阶段晋级写入审计链（FR-BOND-001 约束保持）
- 向后兼容：BondDynamicsWriter 可选注入 stage_engine=None，旧调用路径零影响

**文档更新**：
- PRD.md：M13-A~D 状态更新为 DONE；本收口条目填写完成
- DevTasks.md：TASK-0162~0165 状态均为 DONE；M14 任务卡（TASK-0166~0170）已写入
- requirements_trace.md：R-121~R-130 已补写（M12 R-121~R-125；M13 R-126~R-130）
- docs/50_release/m13_release.json：已生成（release_ready=true）

M14 通道 + 外显 + MCP 治理收口：
M14-A 微信通道 I/O 事件化：FR-CHANNEL-001（WeChat inbound/outbound 建模为确定性通道事件 + 审计串联 + thread_key/request_id 可追溯）— **DONE**（2026-03-04）
M14-B VTS 外显确定性映射：FR-AVATAR-001（emotion_tag→expression_id 固定枚举映射 + 非法 tag 拒绝 + 可回放映射表）— **DONE**（2026-03-04）
M14-C MCP Provider 治理完整验收：FR-MCP-001（MCP provider 元数据同步 + READ/WRITE 分级 + ToolBroker 单入口 + 审计串联 + AIRLOCK 出站最小化）— **DONE**（2026-03-04）
M14-D M14 发布清单 — **DONE**（2026-03-04：1421 passed；m14_release.json 已生成）

M15 语音链路深化完整验收：
M15-A ASR 事件化上送强化：FR-VOICE-001（ASRTranscriptEvent：alternates + evidence_ref + low_confidence 显式标注）— **DONE**（2026-03-04）
M15-B TTS 播报队列强化：FR-VOICE-002（TTSPlaybackEvent：request_id 关联 + 失败 reason_code + stage）— **DONE**（2026-03-04）
M15-C Barge-in 可打断强化：FR-VOICE-003（BargeInEvent 审计记录 + ASR 不被阻塞）— **DONE**（2026-03-04）
M15-D Edge 兜底小模型门控：FR-VOICE-004（FallbackModelGate：默认卸载/按需加载/draft_only 标记/快速失败）— **DONE**（2026-03-04）
M15-E Edge 资源闸门强化：FR-VOICE-005（ResourcePreemptionEvent：优先级抓占审计 + ASR 始终保证）— **DONE**（2026-03-04）
M15-F M15 发布清单 — **DONE**（2026-03-04：1534 passed；m15_release.json 已生成）

M16 Canon 治理强化 + 记忆检索优化 + 调度器持久化：
M16-A Canon 治理 API 强化：FR-CANON-GOV-001（canon_routes.py 依赖注入重构 + approve/reject 事务包裹 + _get_canon_store 竞态修复）— **DONE**
M16-B 长期记忆检索强化：FR-MEM-PAGE-001（list_proposals() cursor-based 分页 + 深页码性能优化）— **DONE**
M16-C 调度器任务持久化：FR-SCHED-PERSIST-001（NatsumeScheduler CRON 任务 SQLite 持久化 + 重启后自动恢复）— **DONE**
M16-D M16 发布清单 — **DONE**

M17 监控面板 v3 — 全服务监控 + Dify 风格链路可视化（前端重建）：
M17-A 前端架构重建：FR-OBS-003（多页面 SPA 架构 + 左侧导航 + Dify 简约风格设计系统）— **DONE**（2026-03-04）
M17-B 全服务健康总览页：FR-OBS-004（双端所有服务状态卡片：ASR/TTS/VTS/LLM/Neo4j/SQLite/Chroma/Scheduler 等 + 实时健康轮询）— **DONE**（2026-03-04）
M17-C 会话列表页：FR-OBS-005（会话列表 + 搜索过滤 + 分页 + 状态徽章）— **DONE**（2026-03-04）
M17-D Dify 风格链路可视化页：FR-OBS-006（节点流图展示：每个节点卡片含状态/耗时/入参/出参展开面板 + 连线动画）— **DONE**（2026-03-04）
M17-E 指标大盘页：FR-OBS-007（请求趋势图 + 拒绝率趋势 + reason_code 分布 + 服务延迟分布）— **DONE**（2026-03-04）
M17-F M17 发布清单 — **DONE**（2026-03-04：React+Vite SPA 构建成功；四页面全量可用；m17_release.json 已生成）

M18 监控面板 v3 后端 API 扩展（为 M17 提供数据支撑）：
M18-A 全服务健康聚合 API：FR-OBS-008（GET /obs/services/health — 聚合双端所有服务健康状态 + 延迟 + 版本）— **DONE**（2026-03-04）
M18-B 会话列表 API：FR-OBS-009（GET /obs/sessions — 分页会话列表 + 过滤 + 统计摘要）— **DONE**（2026-03-04）
M18-C 链路节点详情 API：FR-OBS-010（GET /obs/trace/{request_id} — 结构化节点列表含入参/出参/耗时/状态）— **DONE**（2026-03-04）
M18-D 指标时序 API：FR-OBS-011（GET /obs/metrics/timeseries — 多维度时序数据：请求量/拒绝率/延迟分位数）— **DONE**（2026-03-04）
M18-E M18 发布清单 — **DONE**（2026-03-04：26 个新增测试全部通过；1577 全量通过；m18_release.json 已生成）

M19 可观测链路深化 + 代码审查修复收口：
M19-A~F 代码审查 P0/P1 修复（BUG-BE-001/002、BUG-FE-001、WARN-BE-005、WARN-TEST-001/002）— **DONE**（2026-03-04：1598 passed；m19_release.json 已生成）

M20 Agent 自我感知 + 记忆保护 + 混合检索增强：
M20-A AgentHealthCheck 心跳精神状态检测：FR-OBS-001（SchedulerEngine 心跳任务 + HEARTBEAT_ALERT 审计 + list_health API）— **DONE**（2026-03-05：519 unit passed）
M20-B ContextCompressor 压缩前静默保存：FR-MEM-005 扩展（COMPRESS_BEFORE_SAVE 审计事件 + pre_compress_save_enabled 配置）— **DONE**（2026-03-05）
M20-C Episodic 混合检索增强：FR-MEM-003/005（向量 70% + FTS5 30% 权重融合 + HybridFusionStrategy + 0021_episodic_fts5.sql）— **DONE**（2026-03-05：1648/1661 passed，11 失败待 M21 修复）

M21 测试基线修复 + Canon 向量投影追踪 + 发布清单：
M21-A release_manifest.py `_load_model_plane_meta()` 优雅降级修复（P1：9 个失败用例根因）— **DONE**（2026-03-05：重建 model_profiles.yaml + node_registry.yaml，添加 logging，9 个 P1 用例全部恢复）
M21-B P2 代码质量修复（panel_page 类型注解 + Web UI contract CSR 适配）— **DONE**（2026-03-05：panel_page 添加 -> RedirectResponse 注解，Web UI contract 改为 SPA 特征检测）
M21-C Canon 向量投影模块需求追踪补写（R-156~R-157）+ contract 测试（T-CVP-CONTRACT-01~06）— **DONE**（2026-03-05：20 个 contract 测试全部通过）
M21-D M21 发布清单（目标：≥1661 passed，0 failed；m21_release.json）— **DONE**（2026-03-05：1678 passed，2 skipped，0 failed）

M22 代码质量 Warning 清零：
M22-A HTTP_422_UNPROCESSABLE_ENTITY DeprecationWarning 修复（tool_routes.py + scheduler_routes.py 共 4 处）— **DONE**（2026-03-05）
M22-B asyncio.get_event_loop() DeprecationWarning 修复（test_canon_cursor_pagination.py + test_canon_routes_tx.py 共 6 处；test_webui_integration.py test_webui_routes 重命名）— **DONE**（2026-03-05）
M22-C M22 发布清单（1677 passed，2 skipped，0 failed，0 warnings；m22_release.json）— **DONE**（2026-03-05）

M23 FTS5 中文检索修复 + Edge 心跳探针集成：
M23-A FTS5 中文词组检索修复：FR-MEM-003/005（FullTextSearcher._split_chinese_query 字符级分割；index_memory/search 同步分割；fts_score 归一化 bug 修复；T-FTS-01~06）— **DONE**（2026-03-05：21 个新增测试全部通过，FTS5 中文词组命中率从 0% 恢复正常）
M23-B Edge 心跳探针集成：FR-OBS-001（build_edge_transport_probe_statuses 集成到 /health 端点 + AgentHealthCheck 心跳任务；_check_edge_transport_probes + _log_edge_probe_alert；T-HB-07~09；T-HEALTH-EP-01~02）— **DONE**（2026-03-05：7 个新增测试全部通过，孤立函数已集成）
M23-C M23 发布清单（1731 passed，10 skipped，0 failed，0 warnings；m23_release.json）— **DONE**（2026-03-05）

M24 LLM 网关真实接入 + VTS 表情驱动完整链路 + 微信通道完整链路：
M24-A LLM 网关真实接入：FR-LLM-GW-001~003（health_probe/route_call/多模型路由降级/API Key 环境变量/llm_route_log；T-LLM-GW-01~15）— **DONE**（2026-03-05：16 个新增测试全部通过）
M24-B VTS 表情驱动完整链路：FR-VTS-DRIVE-001~003（VTSMappingStore 持久化版本化/VTSRateLimiter 令牌桶热更新/vts_mapping_change_log/vts_rate_limit_log；T-VTS-DRIVE-01~14）— **DONE**（2026-03-05：16 个新增测试全部通过）
M24-C 微信通道完整链路：FR-WECHAT-001~003（WeChatChannelHealth 健康状态管理/频率限制/降级策略/恢复日志；T-WECHAT-01~16）— **DONE**（2026-03-05：19 个新增测试全部通过）
M24-D M24 发布清单（1782 passed，10 skipped，0 failed，0 warnings；m24_release.json）— **DONE**（2026-03-05）

M24 LLM 网关真实接入 + VTS 表情驱动完整链路 + 微信通道完整链路：
M24-A LLM 网关真实接入：FR-LLM-GW-001~003（Cloud LLM Gateway API Key 接入 + 健康探测 + AIRLOCK 出站净化完整链路 + 多模型路由降级策略；T-LLM-GW-01~15）
M24-B VTS 表情驱动完整链路：FR-VTS-DRIVE-001~003（emotion_tag→expression_id 映射表持久化 + VTS RPC 真实调用链路 + 频率限制队列管理；T-VTS-DRIVE-01~14）
M24-C 微信通道完整链路：FR-WECHAT-001~003（inbound 事件解析 + outbound 推送频率限制 + 通道健康状态降级策略；T-WECHAT-01~16）
M24-D M24 发布清单

---

M25 生产就绪加固 + SQLiteLedger 扩展 + 代码质量收口：**DONE（2026-03-05）**
M25-A SQLiteLedger 扩展（log_llm_route 正式方法）：FR-AUDIT-LLM-001（SQLiteLedger.log_llm_route() 正式方法 + llm_route_log 表 + 迁移脚本 + 审计串联）**DONE**
M25-B WeChatChannelHealth GC + VTSMappingStore 生命周期：FR-CHANNEL-GC-001（_outbound_counts 定期 GC + VTSMappingStore 生产 db_path 强制校验 + 生命周期文档）**DONE**
M25-C SQLiteLedger _table_has_column 缓存 + main.py print 修复：FR-PERF-AUDIT-001（_table_has_column 表结构缓存 + main.py 裸 print() 替换为 logger.info()）**DONE**
M25-D asyncio.get_event_loop() DeprecationWarning 修复：FR-COMPAT-001（vts_driver.py:685 改为 asyncio.get_running_loop() + 全量 Warning 清零验证）**DONE**
M25-E M25 发布清单 **DONE**

---

M26 生产就绪 II — 情感驱动交互 + 端到端集成验证 + 运行时健康加固：**DONE（2026-03-05）**
M26-A 端到端集成冒烟测试套件：FR-CORE-001（覆盖 5 条核心链路 + 审计链完整性校验）**DONE（2026-03-05）**
M26-B 运行时健康检查加固（/health 端点完整性）：FR-OBS-008（validate_health_response\(\) + 服务降级 reason_code）**DONE（2026-03-05）**
M26-C LLM 路由配置热更新 + 审计：FR-LLM-GW-003（update_route_config\(\) + llm_route_config_log 表 + 迁移脚本）**DONE（2026-03-05）**
M26-D VTS 映射表热更新 + 版本回滚：FR-VTS-DRIVE-001（rollback_to_version\(\) + VERSION_NOT_FOUND）**CANCELLED（2026-03-05）**
M26-E 情感驱动对话链路：FR-EMOTION-DRIVE-001~003（LLM 输出 emotion_tag → VTS 表情 + TTS 语气联动）**DONE（2026-03-05）**
M26-F VTS 热键配置文件抽离 + 说明文档：FR-VTS-CFG-001~002（configs/vts/vts_hotkey_config.yaml + load_from_config_file() + 说明文档）**DONE（2026-03-05）**
M26-G mood_state → emotion_tag 约束映射：FR-EMOTION-DRIVE-004（MOOD_EMOTION_CONSTRAINTS + mood_to_emotion_constraint() + QA 集成）**DONE（2026-03-05）**
M26-H sub_emotion 细粒度表情驱动：FR-EMOTION-DRIVE-005（sub_emotion 字段 + sub_emotion_to_hotkey() + 15 个热键充分利用）**DONE（2026-03-05）**
M26-I emotion_intensity 强度参数：FR-EMOTION-DRIVE-006（emotion_intensity 0.0~1.0 + TTS 语气幅度 + VTS duration_ms 联动）**DONE（2026-03-05）**
M26-J M26 发布清单 **DONE（2026-03-05）**

---

## §4.29 SQLiteLedger LLM 路由审计扩展（FR-AUDIT-LLM-001~002）

### FR-AUDIT-LLM-001：log_llm_route() 正式方法

- 用户故事：作为系统运维，我希望 LLM 多模型路由决策有专用审计表，可追溯每次路由选择（主模型/备用/降级原因/延迟）。
- 输入：`request_id` / `model_id` / `reason` / `latency_ms` / `fallback_used` / `ts_ms`
- 输出：写入 `llm_route_log` 表（含 `id` / `request_id` / `model_id` / `reason` / `latency_ms` / `fallback_used` / `ts_ms`）
- 约束：
  - 必须有 SQL 迁移脚本（`0039_llm_route_log.sql`）；
  - `LlmGatewayContract._log_route_decision()` 改为直接调用 `ledger.log_llm_route()`，不再使用 `hasattr` 检查；
  - 写入失败不影响主流程（`try/except` 兜底）。
- 验收：
  - T-AUDIT-LLM-01：`log_llm_route()` 写入成功，`llm_route_log` 表有记录；
  - T-AUDIT-LLM-02：`fallback_used=True` 时记录正确；
  - T-AUDIT-LLM-03：`ledger=None` 时不崩溃；
  - T-AUDIT-LLM-04：迁移脚本幂等（重复执行不报错）。

### FR-AUDIT-LLM-002：llm_route_log 时间窗口查询

- 输入：`start_ts_ms` / `end_ts_ms` / `model_id`（可选过滤）
- 输出：`llm_route_log` 记录列表（分页，最多 100 条）
- 约束：使用 `fetch_by_time_range()` 统一接口。
- 验收：T-AUDIT-LLM-05：时间窗口查询返回正确记录数。

---

## §4.30 通道 GC + 生命周期加固（FR-CHANNEL-GC-001~002）

### FR-CHANNEL-GC-001：WeChatChannelHealth._outbound_counts GC

- 用户故事：作为系统运维，我希望微信通道健康管理器不会因长期运行导致内存泄漏（thread_key 无限增长）。
- 输入：`gc_interval_s`（GC 间隔，默认 3600 秒）/ `inactive_threshold_s`（不活跃阈值，默认 86400 秒）
- 输出：定期清理超过 `inactive_threshold_s` 无活动的 `thread_key`，写 `wechat_gc_log`
- 约束：
  - GC 不影响正在活跃的 `thread_key`；
  - GC 操作持有 `_lock`，线程安全；
  - GC 结果写审计（清理数量 + 时间戳）。
- 验收：
  - T-CHANNEL-GC-01：GC 后过期 `thread_key` 被清理；
  - T-CHANNEL-GC-02：活跃 `thread_key` 不被清理；
  - T-CHANNEL-GC-03：GC 写审计日志。

### FR-CHANNEL-GC-002：VTSMappingStore 生产 db_path 强制校验

- 输入：`db_path`（生产环境必须非 `:memory:`）
- 输出：`db_path=":memory:"` 时写 `WARNING` 日志（不阻断，但可观测）
- 约束：不破坏现有测试（测试中使用 `:memory:` 仍合法）。
- 验收：T-VTS-STORE-01：`db_path=":memory:"` 时写 WARNING 日志。

---

## §4.31 性能与兼容性加固（FR-PERF-AUDIT-001 / FR-COMPAT-001）

### FR-PERF-AUDIT-001：_table_has_column 表结构缓存

- 用户故事：作为系统运维，我希望 SQLiteLedger 在频繁调用时不因 `PRAGMA table_info` 重复执行而产生性能瓶颈。
- 输入：`table_name` / `column_name`
- 输出：缓存表结构信息（`_table_schema_cache: dict[str, set[str]]`），首次查询后缓存，后续直接命中
- 约束：
  - 缓存在 `__init__` 时初始化为空字典；
  - 迁移脚本执行后自动清空缓存（`_invalidate_schema_cache()`）；
  - 缓存不跨连接共享（实例级别）。
- 验收：
  - T-PERF-AUDIT-01：第二次调用 `_table_has_column` 不执行 `PRAGMA table_info`；
  - T-PERF-AUDIT-02：迁移后缓存被清空。

### FR-COMPAT-001：asyncio.get_event_loop() DeprecationWarning 修复

- 输入：`edge_client/actuators/vts_driver.py:685`
- 输出：改为 `asyncio.get_running_loop()`（在协程中）或 `asyncio.new_event_loop()`（在非协程中）
- 约束：修复后全量测试 0 warnings。
- 验收：T-COMPAT-01：全量测试运行 0 DeprecationWarning。

---


- 用户故事：作为……我希望……（可选，但建议保留）
- 输入/输出（黑盒）：输入是什么；输出结构是什么
- 约束：风险等级、门禁、模式影响（Normal/Gaming/Sleep）、是否可撤销
- 验收标准：至少覆盖 3 类用例
  1) 正向成功
  2) 门禁/拒绝（reason_code 断言）
  3) 故障降级（结构稳定 + stage + reason_code）

建议每个需求条目落地后，都在 Trace 表中写：
- FR-xxx ↔ DevSpec 章节 ↔ 测试编号（T-xx） ↔ 日志字段/表

