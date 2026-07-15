受众：CODEX
标题：TESTPLAN（测试契约索引）

版本：v0.4
日期：2026-07-15

说明：本文件维护稳定测试编号与断言口径。新增编号只代表契约已定义；只有对应测试代码和执行证据存在时才可标记为已覆盖。

## T-Intent
- T-18：Intent 宽松解析 + 严格校验。
- T-19：重放保护（nonce/request_id）拒绝。
- T-20：顺序校验失败拒绝。
- T-21：过期拒绝。
- T-22：失败映射 reason_code 稳定。
- T-Intent-01：intent_log 具备 proposer/model/prompt/tool_visibility 最小字段。
- T-Intent-02：proposal/decision/exec 三类日志字段分离。

## T-LLM
- T-LLM-01：单个合法 JSON；禁止前后缀文本；字段完整。

## T-Audit
- T-Audit-01：intent/governor/skill_exec 三联可串联回放。
- T-Audit-RET-01: Retention/GC must preserve triptych replay chain keys and active undo windows; pruned rows and blocked GC actions must remain auditable.

- T-Audit-03: Request-level replay diagnostics must explain missing links across intent/governor/skill chains with stable reason_code mapping.
- T-Replay-01: Given a request_id, replay tool must output deterministic report fields (`status/reason_code_chain/evidence_refs`) and enumerate missing-link causes (`TRACE_MISSING_*`).

## T-Gov
- T-Gov-01：R1~R7 rule_trace 具备 gate_id/hit/threshold_summary/snapshot_summary/reason_hint 且同输入稳定。

## T-Meta
- T-Meta-01: Hard-state metabolism normalization/evolution must be deterministic, and governor rows must carry replayable snapshot_refs (`governance_snapshot` + `hard_state`) derived from the same runtime snapshot input.

## T-QA
- T-67：格式漂移触发一次重写/降级。
- T-68：伪执行宣告拦截。
- T-69：Canon-as-fact 拦截。
- T-70：故障降级仍输出合法 JSON。
- T-71：超时降级仍输出合法 JSON。

## T-Drill
- T-Drill-01：生产演练包必须对 `cold_start_no_key/provider_down/rollback_conflict` 输出可解析单 JSON 结果，并稳定包含 `scenario_id/status/reason_code_chain/replay_ref` 与故障分类处置口径。

## T-Canon

## T-WCanon
- T-WCanon-01：World Canon 文件同步索引到 `world_canon_doc` 且写 `canon_change_log`。
- T-WCanon-02：World Canon 仅以引用口径注入 authoritative（citation_only）。
- T-WCanon-03：World Canon 文件与索引 version/hash 漂移可检测，坏文件不得静默跳过（需可审计）。

## T-Mem
- T-Mem-01：Episodic 写入无 `evidence_refs` 必失败且有稳定 `reason_code`。
- T-Mem-02：`merge/decay/delete` 全生命周期必须写 `memory_ops_log` 且可回放。

## T-Vault
- T-Vault-01：`reversible=true` 的执行路径必须产出 `manifest_ref`，缺失时稳定拒绝。
- T-Vault-02：manifest digest 失配时撤销拒绝且可审计；GC 不得破坏有效 undo 窗口。

## T-Airlock
- T-39: Egress must include sanitization_report_json (removed_fields/redactions/payload_digest), otherwise reject with AIRLOCK_BLOCKED_EGRESS and log airlock_log.
- T-40: Ingress content must be quarantined and classified (FACT/DRAFT_CODE/DRAFT_INSTRUCTION/UNKNOWN) and never directly execute.

## T-Sandbox
- T-Sandbox-01: DRAFT_CODE sandbox execution must produce structured results (sandbox_id/status/exit_code/stdout/stderr/duration_ms/evidence_ref/code_digest), enforce timeout kill and resource limits with stable reason_code, maintain process isolation (no main-process side effects), and write sandbox_exec_log for audit replay; existing T-39/T-40 must not regress.

## T-Tool
- T-Tool-01: ToolMeta pack is metadata-only (no execution payload), with required fields and context-pack validation compatibility.
- T-Tool-02: Top-K selection only exposes a subset and hides WRITE tools by default; tool_visibility_hash is deterministic for same input.
- T-Tool-03: ToolBroker is the single execution entry; WRITE without valid token must reject (TOKEN_INVALID/CONFIRM_REQUIRED), and executed actions must carry evidence_ref.

## T-MCP
- T-MCP-01: MCP provider tools are synced as governed ToolMeta entries, and MCP WRITE without valid token must reject while successful calls remain auditable by request_id/intent_id/evidence_ref.

## T-Orch
- T-Orch-01: Graph orchestration runtime must validate graph config deterministically (`graph_id/version/entry_node/node_bindings/edges/fallback_edges`), emit stable execution envelope (`graph_status/reason_code/stage/replay_ref`), and classify node-missing/edge-invalid/timeout/dependency-down failures with replayable trace.

## T-Channel
- T-Channel-01: WeChat ingress/egress events are traceable by thread_key/message_id/request_id; duplicate ingress is ignored and failed egress includes reason_code/stage.

## T-Avatar
- T-Avatar-01: VTube Studio talk gate (TALK_START/TALK_END) and emotion_tag->expression_id mapping are deterministic and auditable; invalid tags or free-form action params must be rejected with stable reason_code and stage.
- T-Avatar-02: Host `resolve_vts_expression` must keep `DEGRADED + neutral` fallback stable when Edge is unavailable or emotion tags are unmapped.
- T-Avatar-03: Host/Edge `EMOTION_TO_EXPRESSION_ID` contracts must remain aligned for the active 16-tag mapping set.

## T-ClawdUI
- T-41: ClawdBot UI action with confidence < 0.80 must be blocked; no click side effect is produced and clawd_action_log records confidence/target_desc/reason_code.
- T-42: Redline scene and missing UI element must be rejected with stable reason_code (VISUAL_REDLINE/UI_ELEMENT_NOT_FOUND) and guard stage in clawd_action_log.
- T-43: Successful UI action must produce before/after artifacts and manifest linkage; clawd_action_log stores before_artifact_ref/after_artifact_ref/manifest_ref for replay.

## T-Svc
- T-Svc-01: Service registry health output must be parseable and deterministic; same unavailable kind maps to the same reason_code/stage, and each health row is auditable by request_id in service_health_log.
- T-Boot-01: Startup orchestration must output deterministic boot snapshot (`service_id/owner/required/status/reason_code/stage`), include dependency probe matrix, and keep config/probe failures auditable with stable reason_code mapping.
- T-Infra-01: Nine-node deployment inventory must remain deterministic (`node_id/region/role/services/health_endpoint/key_profile_id`), required-service coverage and role conflicts must be rejectable with stable reason_code, and node probe timeout must map to stable `reason_code/stage`.
- T-EnvReady-01: Environment readiness report must be deterministic (`env/node_count/key_profiles/services/graph_runtime/readiness_status`), include dependency matrix and drill summaries, and classify partial-ready vs critical-down with stable reason_code while preserving replay references.

## T-Config
- T-Config-01: Host/Edge layered config loading (env/default/local override) must produce deterministic stage/reason_code on invalid inputs, keep cold-start defaults parseable, and map runtime unavailability consistently into service health outputs.
- T-Config-02: Env snapshot export must produce deterministic redacted output (`env/version/digest/override_sources/redacted_keys`), support stable snapshot diff (`added/removed/changed`), and keep audit replay rows queryable by `request_id`.

## T-Key
- T-Key-01: Key-profile based secret loading must resolve `secret_ref` by `provider/node_id/scope`, reject scope mismatch/expired/missing secret with stable reason_code, and keep config snapshots redacted while exposing only `*_key_ref` linkage.

## T-Ret
- T-Ret-01: Embedding contract output is stable and auditable; unavailable/timeout/input-invalid must map to deterministic `reason_code` (`RETR_EMBED_UNAVAILABLE/RETR_EMBED_TIMEOUT/RETR_EMBED_INPUT_INVALID`) under `S_RET_EMBEDDING`, and each call must write `retrieval_event_log`.
- T-Ret-02: Reranker contract keeps evidence candidates shape stable and auditable; unavailable path degrades with deterministic reason_code/stage.
- T-Ret-03: Evidence pack fusion output is schema-stable and replayable by `request_id/intent_id/evidence_ref`, including degraded retrieval paths.

## T-Chroma
- T-Chroma-01: Chroma external runtime config/probe contract must be deterministic (`endpoint/tenant/database/collection_profiles/default_collection`), and endpoint-down/collection-missing/vector-dim-mismatch failures must map to stable `reason_code/stage`; collection governance must enforce configured vector dimensions.

## T-Embed
- T-Embed-01: Embedding runtime routing config must be deterministic (`provider/model_id/dim/timeout_ms/node_selector`), multi-provider route selection must be stable by node selector, and unavailable/timeout/dim-drift failures must map to stable `RETR_EMBED_*` reason codes with auditable expected-vs-actual dim fields.

## T-Rerank
- T-Rerank-01: Reranker runtime routing config must be deterministic (`provider/model_id/top_k/timeout_ms/node_selector`), score normalization and fallback must stay stable, and model-down/timeout/invalid-score-range failures must map to stable `RETR_RERANK_*` reason codes with auditable `expected_top_k` payload linkage.

## T-Bond
- T-50: Bond SSOT updates can only be applied through the single-writer path (`bond_dynamics`) and must create replayable `event_id` with `evidence_refs`.
- T-51: Bond update requests without `evidence_refs` must be rejected with stable reason_code and logged in `bond_update_log`.
- T-52: Runtime single-writer boundary for Neo4j must reject non-`bond_dynamics` writers with stable reason_code.
- T-53: Bond events and graph relations (`HAS_EVENT/ABOUT/AFFECTS`) must be replayable by `user_id` and `event_id`.
- T-54: Neo4j bond constraints/index assets must be loadable from migrations, and summarizer path must not perform SSOT writes.
- T-Neo4j-01: Neo4j external deployment config/probe must be deterministic (`uri/database/auth_ref/pool/tls/enabled`), and auth/schema/leader failures must map to stable reason_code with migration-log audit linkage.
- T-74: Bond summary must be derived-only and must not contain sensitive numeric values; only bucketed fields plus replay refs (`event_id/evidence_refs`) are allowed.
- T-75: Bond summary must inject into `prompt_context.bond_summary` slot without mixed-slot leakage and remain valid under `T-CP-01` isolation checks.

## T-SSOT
- T-SSOT-01: Static scan must detect and block non-single-writer Neo4j write paths (`write_bond_event` outside `bond_dynamics`) without relying on external services.

## T-Migrate
- T-Migrate-01: SQLite/Bond migration runners must enforce idempotent apply, detect catalog gaps and schema drift, reject rollback version regressions with stable reason_code, and emit auditable records containing `migration_id/status/reason_code/latency_ms`.

## T-Voice
- T-Voice-01: `asr_chunk` event schema is deterministic and auditable; missing fields, out-of-order seq, and throttled ingress are stably rejected, while low confidence is accepted with explicit marker and stable reason_code/stage for replay.
- T-Voice-02: `speak -> synth -> playback` queue is deterministic and auditable; synthesis/playback failures emit stable `reason_code/stage` while preserving replay linkage by `request_id/session_id`.
- T-Voice-03: `barge_in` interrupts active playback immediately and leaves auditable interruption events; ASR ingress remains unblocked and replayable in the same session.
- T-Voice-04: Edge fallback LLM endpoints (`presence/asr_compile/retrieval_hints/context_compress`) must keep `draft_only=true`, reject decision fields and false execution claims, and return stable fast-fail reason_code/stage when unavailable.
- T-Voice-05: Resource gate enforces priority `ASR > PLAYBACK > TTS_SYNTH > FALLBACK_LLM` with mutex under pressure, exposes `vram/queue` metrics in gate events, and keeps preempt/reject decisions replayable.

## T-RPC
- T-RPC-Event-01: Edge->Host event reporting protocol must keep deterministic schema (`event_type/source/ts_ms/payload/evidence_ref`), support cache/retry/backfill on network interruption, and preserve replay linkage after recovery.

## T-Edge
- T-Edge-RPC-01: Edge RPC envelope authentication/replay protection must enforce schema + HMAC + ts window + nonce uniqueness with stable reason_code mapping (`EDGE_RPC_SCHEMA_INVALID`/`EDGE_RPC_AUTH_INVALID`/`EDGE_RPC_AUTH_EXPIRED`/`REPLAY_DETECTED`), and support dev/test insecure-auth fallback without breaking schema checks.
- T-Edge-RPC-02: Edge RPC gateway endpoints must return deterministic response envelope (`success/status/reason_code/detail/event/ts_ms`) and reject disabled capability calls with auditable reason_code/stage.
- T-Edge-Health-01: Edge health endpoint must expose stable capability matrix + resource snapshot + component health + redacted config snapshot, consistent with config switches and fallback defaults.
- T-Edge-Start-01: `python -m edge_client` startup/shutdown must be deterministic (bind host/port, emit config snapshot, graceful close of ASR/TTS/VTS resources) and leave no stale runtime lock/process state.
- T-Edge-Voice-01: Edge ASR upstream pipeline must batch-report events to Host with retry/cache/backfill semantics, preserving deterministic schema and replay linkage during network interruption and recovery.
- T-Edge-Voice-02: Edge barge-in + VTS linkage must deterministically interrupt playback, produce `VOICE_INTERRUPTED`-class audit evidence, and restore talk gate/expression terminal state.
- T-Edge-Fixture-01: Edge fixture naming must follow `tests/fixtures/edge/{auth|gateway|health|voice}/{good|bad}/scenario_expectation.json` with expectation suffix in `{accept|reject|degraded}` and each `T-Edge-*` id mapped to success + reject/degraded samples.

## T-Vision
- T-VisionGate-01: Gaming mode must block screen-monitor/OCR requests with stable reason_code/stage and auditable event linkage; response must not claim unsupported visual observations.
- T-Vision-01: Non-gaming OCR output must remain schema-stable (`request_id/ts_ms/source_ref/engine_id/text_blocks[]` with `text/bbox/conf`) and source_ref must be replayable via vision_event_log.
- T-Vision-02: Camera recognition events must be replayable by evidence_ref, include explicit low-confidence marking in object outputs, and degrade/reject with stable reason_code/stage when evidence is missing or detector is unavailable.
- T-Vision-03: Multi-detector registration and aggregation must produce stable detections[] with per-detector evidence_ref, partial degradation (PARTIAL) when some detectors fail, and ALL_FAILED when none succeed; existing T-Vision-02 must not regress.

## T-CI
- T-CI-01: Unified CI delivery gate must classify failures across `contract/e2e/static_scan/doc_gate` with deterministic report fields (`job_id/check_id/reason_code/failed_artifact`).

## T-Release
- T-Release-01: Release checklist for `M0~M3` must remain deterministic (`milestone/assets/checksums/required_services`), and rollback mapping must resolve `release_id/rollback_target/impact_scope`.

## T-NextGen-Critical

- T-GR-RUNTIME-01：Host Chat/Input/Gateway、Scheduler、Internal Agent 与 Operator Command 的真实 route matrix 必须全部生成 `WorkOrderV3 + BudgetPolicy + TaskGraph + node-bound grant + Verification + Acceptance + Receipt`；至少执行每路首个真实 admission，startup-only 测试不能通过本 Gate。
- T-PERSONA-FENCE-01：NE-01 safety fence 后，Bond stage/milestone、interaction cycle、emergent weekly 和后续新增入口在 `PersonaEngine.apply_growth_delta()` 中央 authority 边界全部 fail closed，trait mutation 为 0；静态扫描证明不存在绕开中央门禁的新 writer，运行时负测覆盖全部已知入口。
- T-PERSONA-PROPOSAL-01：NE-06 replacement 后，旧 Persona growth caller 只生成 `FeedbackProposal`，并完成 proposal → Lab/shadow → Review → versioned activation → rollback；普通后台任务无直接 trait mutation authority。
- T-PLAN-PROD-01：Research/Code 使用真实 workload、真实 source/repository、真实 model/verifier、隔离 workspace、实际成本和延迟比较 baseline 与 PlanningSearch；synthetic fixture 不能计入，未执行候选不得复用 baseline outcome。
- T-PLAN-CAL-01：Pentest/通用 L3 必须按虚拟状态动态生成后继，prediction/baseline/candidate/actual outcome/cost 跨 run durable；达到预注册样本、误差、安全与 uplift 门槛前 production influence 为 0。
- T-NE-FINAL-01：NE-07 聚合所有 Target Architecture Gate；任一 Target Gate 未满足时只能 `FAIL` 或精确的 `ACCEPTANCE_PENDING_EXTERNAL`，历史 receipt、shadow 完成或早期 wave 状态不能产生 `PASS`。

## T-NLE（统一 Runtime 与数字生命安全）

- T-NLE-01：架构基线可复现；Domain→Core、raw thought durable write 等禁止依赖可被静态检测，测试不得夹带任务范围外改动。
- T-NLE-02：Pentest 历史知识回填的计数、source hash、标准化内容和检索样本一致；domain mode 下 WorldCanon 直接读写为 0。
- T-NLE-03：Canon Apply 强制绑定 token/subject/version/expiry/once；缺失、过期、串用、版本漂移和重复消费均拒绝；direct writer 为 0。
- T-NLE-04：WorkOrder v2/v3 round-trip 兼容；TaskGraph/Verification/Review schema 稳定；非法版本和非法状态迁移 fail closed。
- T-NLE-05：每个 graph revision 只规划并冻结一次；失败重规划创建同一 graph 的 `revision+1`；顺序/并行/混合依赖正确；Child WorkOrder→SubAgentRequest lineage/授权完整；cycle 返回 `PLAN_CYCLE`。
- T-NLE-06：TaskStore 的 CAS、attempt、lease、heartbeat、重启恢复、业务幂等 reservation、budget reservation/usage 和 transactional outbox 可并发验证。
- T-NLE-07：Task scheduler 的 READY、依赖、retry/backoff、cancel 和 recovery 决策可回放；重放/预览不产生真实副作用。
- T-NLE-08：required verification 的 `FAIL/ERROR/NOT_RUN` 均不得进入 `VERIFIED/ACCEPTED`；命令验证受 allowlist/timeout/workspace policy 约束。
- T-NLE-09：每条 acceptance criterion 都有 evidence/verdict；确定性 FAIL 不可被 LLM Critic 覆盖为 ACCEPT。
- T-NLE-10：PRE/POST Review 使用不同恢复阶段；重启、过期、串用和重复 resume 均 fail closed；POST resume 不重放副作用。
- T-NLE-11：canonical 切流只影响新任务；legacy drain 可对账；跨执行器 resume 和重复副作用为 0；canary 可回退。
- T-NLE-12：关键 Runtime Event 与状态同事务写入；cursor reconnect 无缺失，重复事件幂等；raw thought/secret/cookie/private body 为 0。
- T-NLE-13：Context refs 按角色授权；token 预算、实际消耗、截断原因和污染拒绝可复现。
- T-NLE-14：Life schema migration 幂等；expected-version OCC 在 20 个并发写入下无 silent overwrite。
- T-NLE-15：WorldProjection 无反写接口；source/confidence/observed/expires/conflict/epistemic type 完整且过期生效。
- T-NLE-16：事件可回放到 Appraisal、affect/drive 与表达策略；Bond 保持单写者，rift/repair/boundary 语义不丢失。
- T-NLE-17：SceneDecision 实际影响 Surface/proactive；ActionArbiter 只能收紧 Governor，不能覆盖用户显式任务。
- T-NLE-18：Capability risk adapter 输出稳定；自动只能降级；人工升级需要评测证据和 Review，不能绕过静态风险上限。
- T-NLE-19：DegradationState 可进入、恢复并携带证据与影响能力；核心故障注入不会开放受影响高风险能力。
- T-NLE-20：IdentitySnapshot 可比较、可审查、无私密正文；重大差异阻断发布并可回滚至前一 release ref。
- T-NLE-21：Execution/Trace/Review UI 能回放复杂任务并正确处理 approve/reject/expired，不提供绕过 token 的恢复入口；lint/typecheck/read-model unit 与既有 E2E 均通过。
- T-NLE-22：Life UI 正确区分事实、推断、过期和冲突，并展示 proactive dry-run 的来源与裁决。
- T-NLE-23：Capability/Health/Identity/Security Lab UI 不提供自动升级或未审批身份变更，不泄漏私密正文；非 Lab 模式不暴露可执行 Pentest 高风险入口。
- T-NLE-24：稳定测试、迁移、重启、SSE、故障注入、canary/drain 和 rollback drill 全部产生可审计 Seal 证据。

## T-ARCH（完整架构发布 Gate）

说明：编号定义不代表代码已实现；每项必须同时具有自动化测试、可审计执行证据和回滚证据，才可标记为通过。执行顺序只由 `docs/30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md` 决定。

### Secure Ingress 与平台安全

- T-ARCH-01：Host、Obs、Gateway 与 Bridge 的所有写接口统一校验 `principal/scope/signature_or_token/rate_limit` 并写审计记录；缺失、过期、串用、重放和越权请求全部 fail closed，Obs 不再提供未治理写旁路；Pentest 必须使用真实 principal/绑定 token 且只能在批准 Lab scope 运行。
- T-ARCH-02：LINE、Feishu、WeCom 等 webhook 使用原始 body/header 完成平台验签；Gateway ingress/reply 具备双向认证、durable inbox/outbox、持久幂等、ack/retry/DLQ，重启和重复投递不产生重复外发。
- T-ARCH-03：Service Catalog/port registry 能静态检测端口冲突、公开绑定和未认证本地桥；MateEngine/VRM 等冲突消失，设备桥默认 loopback、协议版本明确、secret/config snapshot 全程脱敏；新增 `edge_client → host_brain.*` 实现导入由 CI 阻断并登记既有债务；配置的模型 provider 必须成功构造或以稳定原因失败，禁止缺失导入/宽泛异常触发静默伪降级；未具 digest/signature/SBOM/quarantine/sandbox 的 Skill/Provider bundle 不能热导入 Host。

### Contracts、Kernel、Composition 与依赖治理

- T-ARCH-04：生成目标依赖图/SCC 与批准矩阵；建立“不得新增双向边、底层→`app` 边或 Edge→Host 实现边”的 CI 护栏，既有债务逐项绑定 owner/remove-by；全量归零在 T-ARCH-22/T-ARCH-29 复验。
- T-ARCH-05：共享 contracts 的 command/event/error/config/risk/embodiment envelope 支持版本化 round-trip 和 consumer-driven compatibility；未知主版本、缺失必填字段及不兼容降级稳定拒绝。
- T-ARCH-06：Composition Root 对服务创建、启动、健康、降级和关闭只有一个 owner；Host/Worker/Query profile 可隔离装配，Edge 共享合同可在不导入 Host 实现时验证；同一配置输入产生确定且脱敏的 topology/config snapshot，Edge 完整独立安装由 T-ARCH-22 验收。

### Host Application 与 Task/Agent Runtime

- T-ARCH-07：HTTP/CLI/Channel adapter 不承载 domain、repository 或 provider 真相；每个写命令均通过 Application Port，同一 action 只产生一个 AuthorizationGrant、一个 Task/Attempt lineage 和一个副作用 receipt。
- T-ARCH-08：复杂任务只由 Canonical Task/Agent Runtime 规划和调度一次；legacy executor 仅作兼容 facade，shadow compare 不产生副作用，崩溃恢复、lease 竞争和重复提交保持 exactly-once business effect。
- T-ARCH-09：Host 主链巨石（Ingress、Conversation、Executive、Task/Agent Runtime、Surface facade）已按行为切片，其他 `>1500` 行文件均有 owner/remove-by；禁止新增 `>800` 行混合职责文件，legacy facade 具调用遥测和删除版本，最终 usage=0 由 T-ARCH-29 复验。

### Digital Life Kernel、Surface 与 Identity

- T-ARCH-10：Digital Life 使用单一时钟、版本化状态、OCC 和 journal；并发推进无 silent overwrite，相同事件可确定性重放到相同 affect/drive/appraisal/relationship 状态，长期字段 writer 唯一。
- T-ARCH-11：Surface Context 构造与 WorldProjection 查询保持只读；Scene/Proactive/Embodiment 决策只通过 durable outbox 产生动作，ActionArbiter 只能收紧 Governor 权限且不能覆盖用户显式任务。
- T-ARCH-12：Identity 采用 immutable baseline + versioned overlay，snapshot/diff/activate/rollback 可审查且不含私密正文；`RelationshipProjection` 是唯一写模型，Bond/Neo4j 仅为可重建投影，WorldProjection 无反写接口。跨版本 migration/regression 与发布阻断由 T-ARCH-28/T-ARCH-30 验收。

### Memory、Knowledge、Event 与 Persistence

- T-ARCH-13：Storage Catalog 为每张表、文件和索引声明 owner、writer、retention 与 migration scope；production 直接访问 Ledger 私有连接和 inline DDL 为 0，跨存储事务只经明确 UoW/outbox。
- T-ARCH-14：component-scoped migration catalog 无重复、缺口和 drift；空库、受支持旧库、expand/contract、备份恢复和 rollback drill 全通过，失败不会留下半迁移 schema。
- T-ARCH-15：关键状态与 Event/Outbox 同事务提交，projection 可幂等重建并对账；durable payload 不含 raw thought/secret/cookie/private body，领域知识隔离且 Canon promotion 校验审批内容 hash。

### Domain Pack 与 Capability Platform

- T-ARCH-16：Domain Pack manifest/SDK 的 command、tool、state、knowledge、policy、projection 与 UI contribution 可版本校验、隔离加载、停用和回滚；Domain 不反向依赖 Application/Infrastructure，也不拥有平台级存储或线程。
- T-ARCH-17：Pentest 仅在授权 Lab 隔离环境运行，并复用共享 WorkOrder/TaskGraph/RunStore/Governor；普通聊天检索不到 Pentest 知识，Pentest 对 WorldCanon 的直接读写为 0，知识提升必须经 owner review。
- T-ARCH-18：Skill/Tool/MCP/Pentest 等 registry 通过统一 Capability Catalog 和公开 Broker port 适配；风险尺度转换稳定且保留来源证据，Domain Pack 不得自建 policy/token/audit gate。全局唯一 Broker/Grant/Token 与旁路归零由 T-ARCH-21 验收。

### Model、Prompt、Governor 与 Security Plane

- T-ARCH-19：统一 Model Gateway 独占 provider routing、timeout/retry/circuit/cache/streaming/budget/usage；provider 故障和模型降级可观测且不被宽泛异常静默吞掉，同一请求的 attempt lineage 可完整回放。
- T-ARCH-20：Prompt/Context asset 具备版本、digest、owner、适用模型与注入边界；私有 helper 跨层导入为 0，污染输入、越权 context ref、PII/secret 泄漏和提示降级均被稳定检测并审计。
- T-ARCH-21：AuthorizationGrant 绑定 subject/action/scope/content_hash/version/expiry/once；所有执行经过唯一 Capability/Tool Broker、Grant/Token 与 audit gate，executor 旁路为 0；Skill/Provider bundle 必须经过 digest/signature/SBOM/quarantine/sandbox/owner approval/canary/rollback，未受信代码不能在 Host 主进程热导入。

### Edge、Gateway、Voice、Avatar 与 Device Runtime

- T-ARCH-22：Edge 是 Desktop Pet、MateEngine、VTS、VRM、Voice 等设备副作用的唯一 authority；Host 无直连旁路，`EmbodimentCommand` 版本和 capability handshake 一致，断线重试不重复动作。
- T-ARCH-23：ASR、TTS synthesis、audio playback、lip-sync 与 GPU/model worker 具有独立生命周期、资源预算和 watchdog；barge-in、worker 崩溃、显存压力与重启恢复保持队列/会话/审计语义一致。
- T-ARCH-24：VTS/VRM/MateEngine/Electron adapters 使用唯一端口和认证协议；emotion/expression profile 由中心 ontology 确定性编译，adapter 缺失或映射漂移时安全降级且不启用不安全浏览器配置。

### Query/Observability Plane 与 Operator Console

- T-ARCH-25：Query/Observability Plane 对 operational store 只读，写路由和私有 Ledger SQL 依赖为 0；read model 有 schema version、owner、cursor 和重建/对账证明，缓存具备明确失效与容量边界。
- T-ARCH-26：OpenAPI 不存在重复 method/path，生成的 UI client 与后端 schema 通过兼容测试；route visibility 和命令执行均按 role/capability 授权，高风险确认不能替代后端 token。
- T-ARCH-27：Task/run/event/trace/metric/log 共享 correlation IDs；Operator Console 的 replay、review、health、device 与 release 视图通过 lint/typecheck/unit/component/a11y/e2e，并在目标数据量下满足声明的查询 SLO。

### Evolution Lab、Decompat、Release 与最终封板

- T-ARCH-28：Evolution Lab 的 candidate/feedback/counterfactual/scenario/benchmark 默认隔离且无生产副作用；Skill 或 Identity migration/promotion 必须具备版本化 schema、可重复 regression/replay、风险证据、owner review、canary、rollback target 和自动回滚阈值。
- T-ARCH-29：所有 compatibility facade、旧 writer、重复 planner/runtime 和手工 UI contract 均有 usage=0 与删除证据；全部 production `>1500` 行混合职责巨石已拆分或具备批准 ADR，新增 `>800` 行混合职责文件为 0；源码树不跟踪易漂移 hashed build output，发布 artifact 可由锁定输入确定性重建。
- T-ARCH-30：ReleaseManifest v2 包含 commit、packages、schema、protocol、model/prompt/avatar assets、config digest、SBOM、gates 和 rollback compatibility；组件矩阵 CI、security/chaos/performance、backup/restore、升级/降级及最终 rollback drill 全部生成 Seal 证据。
