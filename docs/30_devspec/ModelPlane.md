受众：CODEX
标题：模型平面（Model Plane）规格说明（9 云节点配置、路由、审计与降级）

# 模型平面（Model Plane）规格说明
版本：v1.1
日期：2026-02-28

定位：本文件属于 DevSpec 子规格，只写“怎么做 + 如何验证（测试点/日志点/契约点）”。  
目标：把“9 云节点怎么分工、怎么路由、怎么降级、怎么最小化出网、怎么审计回放”写成稳定契约，允许你以后换模型但不换治理。

---

## 0. 硬约束（不可违反）

0.1 主权与事实
- 所有 LLM 节点永远不裁决、不执行、不宣告执行事实；任何“已执行/已保存/已确认/已完成”只能由 Host 审计链摘要支撑。
- 云端输出一律降权为草案：不得直通主权写入、不得直通执行。

0.2 出网最小化（AIRLOCK）
- 云端只接收最小必要信息：禁止上行密钥、审计链全文、硬状态原值、私密记忆全文。
- 每次出站必须生成 sanitization_report_ref 与 payload_digest（写入 airlock_log），否则阻断外发。

0.3 工具可见性（渐进式加载）
- LLM 每轮只可见 tool_meta_pack 的 Top-K 工具元数据子集。
- WRITE 类工具默认不可见；即使可见也必须经 Host Governor+Token 才能执行。
- tool_visibility_hash 必须随 intent_log 一并记录，用于追责“当时模型看见了什么”。

0.4 二段式输出（强制）
- 第一段：中文语义内核（产 narrative_zh + intent_draft，边界锁死）
- 第二段：日文化渲染（将 narrative_zh 渲染为日文 C 风格，禁止加料/升级）

---

## 1. 输出契约（Strict JSON）

1.1 CoreDraft.v1（语义/规划类节点输出）
- 单个 JSON 对象
- 字段最小集：
  - narrative_zh: string
  - intent_draft: object|null
  - meta: object（可选；不得承载执行事实）

1.2 RenderDraft.v1（渲染器输出）
- 单个 JSON 对象
- 字段最小集：
  - rendered_ja: string（只做风格渲染）
  - render_meta: object（可选）

渲染禁令：
- 不得新增事实、不得升级承诺强度、不得升级证据强度、不得引入新 intent。

1.3 Verifier.v1（校验节点输出）
- 单个 JSON 对象
- 字段最小集：
  - verdict: PASS|FAIL|UNKNOWN
  - issues: [{code, severity, span_hint, note}]
  - recommended_action: ALLOW|REWRITE|DOWNGRADE|ASK_CLARIFY

---

## 2. ContextPack（Host→Cloud）分槽与出网字段

2.1 分槽字段（必须）
- hard_snapshot：桶化（energy_bucket/mood_bucket/sys_mode/safety_bucket），不含原值
- bond_summary：stage/trend/capability_hint，不含数值
- authoritative.world_canon：excerpt 级引用（evidence_level=CANON_REFERENCE_ONLY）
- retrieval.episodic：仅摘要+evidence_refs
- style_pack.style_canon：tone_only=true，unsafe_as_fact=true
- tool_meta_pack：Top-K 工具元数据子集（可空）
- constraints：strict_json_output、no_false_execution_claim、no_canon_as_fact、tone_policy、contract_versions

2.2 AIRLOCK 出站审计字段（必须写）
- sanitization_report_ref
- payload_digest
- removed_fields / redactions 摘要
- destination（provider/region）

---

## 3. 9 云节点（固定分工 + 具体模型落点）

说明：
- node_id/role 固定；provider/model_id 可替换，但必须保持契约版本兼容；若不兼容必须 bump model_profile_id。
- 为可回放与抗漂移：优先使用“可 pin 的具体模型字符串/快照”；仅在明确允许时使用 latest 别名。

3.1 节点清单（v1.1 具体配置）
1）core_reasoner_primary（role=core）
- 用途：主语义推理（叙事+意图草案）
- provider/model_id：openai / gpt-5.2
- 输出：CoreDraft.v1
- 约束：输出尽量短（避免长叙事占预算）

2）core_reasoner_secondary（role=core_backup）
- 用途：主模型超时/限流时的备选（更快更省）
- provider/model_id：openai / gpt-5-mini
- 输出：CoreDraft.v1

3）planner_constraints（role=planner）
- 用途：复杂任务约束拆解（偏结构，不写长叙事）
- provider/model_id：openai / gpt-5.2-pro
- 输出：CoreDraft.v1（narrative_zh 可空）

4）tool_planner（role=tool_planner）
- 用途：在 tool_meta_pack 子集内选择工具与参数草案（不得执行）
- provider/model_id：qwen / qwen3.5-plus-2026-02-15（pin）
- 输出：CoreDraft.v1（intent_draft 必含 tool_id + args 草案）

5）safety_verifier（role=verifier_safety）
- 用途：拦截三类风险：伪执行宣告、承诺升级、证据升级、越权倾向
- provider/model_id：openai / gpt-4.1-mini
- 输出：Verifier.v1

6）consistency_verifier（role=verifier_consistency）
- 用途：检查 Canon 引用口径、混槽倾向、格式漂移倾向
- provider/model_id：gemini / gemini-2.5-flash-lite
- 输出：Verifier.v1

7）renderer_ja_c_style（role=renderer_ja）
- 用途：日文 C 风格渲染（短句、克制、停顿……），不得加料
- provider/model_id：gemini / gemini-2.5-flash
- 输出：RenderDraft.v1

8）workbench_translate（role=workbench_translate）
- 用途：翻译/术语一致性辅助（流水线任务）
- provider/model_id：gemini / gemini-2.5-pro
- 输出：CoreDraft.v1（或后续 TranslateDraft）

9）workbench_code_assist（role=workbench_code）
- 用途：代码协作草案（不得宣称已构建/已通过）
- provider/model_id：deepseek / deepseek-chat
- 输出：CoreDraft.v1

3.2 Host 常驻兜底（不计入云节点，但属于模型平面）
- host_fallback_policy（role=rule_engine_or_tiny_model）
- 目标：3–5 秒开口、云超时退化、最低合规模板
- 约束：不得依赖 GPU；不得产生执行宣告；不得自动触发执行

---

## 4. 路由与链路（LangGraph 视角）

4.1 场景枚举（最小）
- CHAT：闲聊/陪伴
- EXEC_INTENT：执行意图（需要 Governor）
- WORK_TRANSLATE：翻译流水线
- WORK_CODE：代码协作
- VISION_UI：ClawdBot/UI（高门槛）
- CHANNEL_IO：微信/通道（确定性外显，模型只写草案文本）

4.2 两段式输出链路（强制）
- S1：core_reasoner_primary →（必要时 safety_verifier）→ Host QA/Governor
- S2：renderer_ja_c_style → Host RenderGuard
规则：
- intent_draft 只能来自 S1；S2 不得新增/修改 intent_draft。

4.3 3–5 秒开口（在场回应 + 超时退化）
- t0：Host 立即输出“在场回应模板”（不承诺执行），并启动云调用
- t1：core_reasoner_primary 超时 → core_reasoner_secondary
- t2：仍失败或格式漂移 → host_fallback_policy（模板/极小模型）
- 任何降级必须写入 error_log/quality.stage，并带可统计 reason_code（DEPENDENCY_UNAVAILABLE/LLM_FORMAT_DRIFT 等）

4.4 Gaming 模式（显存被游戏占用）
- Host 强制 NO_LOCAL_GPU：仅允许模板/规则引擎/CPU 兜底
- 云端仍可用（受 AIRLOCK、预算与网络波动影响）
- 渲染默认走云端 renderer_ja_c_style；云不可用则退化为 Host 简短日文模板（由 Style Canon 提供），不得“补剧情”。

---

## 5. 成本与长度治理（不写价格，只写可执行预算口径）

5.1 默认限额（建议基线）
- CHAT：Core 输出 max_output_tokens_cap（短），Renderer 输出 max_output_tokens_cap（中短）
- WORK_*：允许更长，但必须启用“结构化输出+分段”策略（由 Flow/编排控制）
- EXEC_INTENT：叙事短、intent_draft 结构完整优先

5.2 触发降本策略的条件（建议）
- 连续 N 轮长输出、或单轮超过 token_cap、或 unit_time_cost 超过阈值 → 降级到 core_reasoner_secondary 或缩短输出模板
- 深度工作站任务（翻译/代码）优先用专用节点，避免污染闲聊预算

---

## 6. 审计字段（必须写入）

6.1 每次云端模型调用（建议写入 llm_call_log 或 intent_log 扩展）
- model_profile_id
- node_id
- provider / model_id（或快照标识）
- prompt_profile_id（仅存引用/哈希）
- input_digest / output_digest
- tool_visibility_hash（若暴露工具元数据）
- latency_ms（可选含 TTFT）
- token_usage（若可得）
- sanitization_report_ref（出站必须有）
- outcome：SUCCESS|TIMEOUT|FORMAT_FAIL|POLICY_FAIL|DOWNGRADED

6.2 intent_log（proposal）必须包含（Intent responsibility）
- proposed_intent_draft（原文）
- proposer_node_id / model_id / prompt_profile_id
- tool_visibility_hash
- lint_result（schema 校验结果）

---

## 7. 回归测试口径（建议编号）

- T-LLM-01：CoreDraft/RenderDraft/Verifier 输出 Strict JSON（单对象、无前后缀、字段最小集）
- T-Render-01：渲染不得加料/不得升级承诺与证据/不得引入 intent
- T-CP-01：context_pack 分槽隔离（world/style/episodic/tool_meta 不混槽）
- T-Intent-01：intent_log 字段最小集（proposer/tool_visibility_hash 等）
- T-Intent-02：proposal/decision/exec 分离（intent_log 不得支撑完成性表述）
- T-Perf-01：超时退化链路（在场回应→主→备→兜底）输出结构稳定
- T-Gaming-01：Gaming 模式下 Host 不依赖本地 GPU（仅兜底策略可用）

---

## 8. 风险备注（与本次对话结论对齐）

- 去限版/abliterated：禁止用于语义内核与裁决相关节点；如必须使用，仅允许用于 renderer_ja，并强制 RenderGuard 校验“不得加料/升级”。
- 多供应商：用于抗单点故障与成本治理，但所有输出仍必须过 Host QA/Governor/审计链。

---

## 7. Edge 端兜底模型（不纳入 Agent 图，仅作为远程能力）
- Edge 3B 量化模型（Ollama）不作为 LangGraph 节点参与编排树；Host 仅以“远程工具/能力端点”的方式调用。
- 调用目的仅限：在场回应、输入清洗、摘要压缩、检索提示（draft-only）。
- 约束：不得产生裁决语义、不得产生执行事实宣告；失败必须可观测并快速回退到 Host 兜底口径。