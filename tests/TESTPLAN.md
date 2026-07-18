受众：CODEX
标题：TESTPLAN（测试契约索引）

版本：v0.7
日期：2026-07-16

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
- T-PLAN-RESEARCH-D20A-01：Research pure-shadow catalog 只能从冻结的公开 source id/ref/digest 与有限 authority/relevance/read-cost metadata 动态生成 query、quick-answer、primary、independent、cross-check、conflict、stop；snapshot/payload 没有专用的本机 locator、source-body、answer-key 或 verifier-decision 字段，constraints 拒绝这些命名 key，但任务文字不是内容级 DLP。`virtual_transition_ready=false` 时标准 domain wiring 不得提供 L3 transition/reward。FrozenResearchCorpusLoader 只能读取可信实验根与 manifest 指定的 canonical repository-relative regular file，读前/读后必须在稳定受信输入状态中检测并拒绝绝对/遍历、ADS colon component、case alias、link/reparse、未列来源、类型/大小/digest 漂移，公开 receipt 不含路径/正文。它不是 hostile concurrent replacement 的原子 TOCTOU 防线，且本 Gate 不会把 snapshot 与 manifest 的 corpus/source id/ref/digest/tier 绑定或证明独立来源；该 handshake 属于 D20b。该 Gate 只证明 D20a 机械边界，不是 T-PLAN-PROD-01 的真实 source/model/verifier/paired-benefit 验收，也不是 OS/tenant sandbox。
- T-PLAN-CODE-D19-01：Code 实验的预注册 pytest argv 只能在 disposable detached worktree 中以 shell-free child 执行；必须拒绝 argv/path/budget drift，核验实际解释器后持久化 portable argv digest，强制 `PASS ↔ exit_code=0`，并把 command/exit/受限输出 digest/耗时与 exact run/arm/plan/budget、两 arm aggregate test/wall limit 和 `VERIFIED_CLEAN` 绑定。child 可能启动前必须预留其冻结 command budget，timeout/error 后同一 arm 不能重试；带 D19 plan 的通用 terminal ledger 也必须拒绝任何 FAIL arm。restart replay 必须拒绝损坏 receipt。该 Gate 不等于独立安全域、hermetic Python、模型/工具预算、crash cleanup recovery 或 `T-PLAN-PROD-01` 的收益验收。
- T-PLAN-CAL-01：Pentest/通用 L3 必须按虚拟状态动态生成后继，prediction/baseline/candidate/actual outcome/cost 跨 run durable；达到预注册样本、误差、安全与 uplift 门槛前 production influence 为 0。
- T-NE-FINAL-01：NE-07 聚合所有 Target Architecture Gate；任一 Target Gate 未满足时只能 `FAIL` 或精确的 `ACCEPTANCE_PENDING_EXTERNAL`，历史 receipt、shadow 完成或早期 wave 状态不能产生 `PASS`。

## T-NLE（统一 Runtime 与数字生命安全）

- T-NLE-01：架构基线可复现；Domain→Core、raw thought durable write 等禁止依赖可被静态检测，测试不得夹带任务范围外改动。
- T-NLE-02：Pentest 历史知识回填的计数、source hash、标准化内容和检索样本一致；domain mode 下 WorldCanon 直接读写为 0。
- T-NLE-03：Canon Apply 强制绑定 token/subject/version/expiry/once；缺失、过期、串用、版本漂移和重复消费均拒绝；direct writer 为 0。
- T-NLE-04：WorkOrder v2/v3 round-trip 兼容；TaskGraph/Verification/Review schema 稳定；非法版本和非法状态迁移 fail closed。
- T-NLE-05：每个 graph revision 只规划并冻结一次；失败重规划创建同一 graph 的 `revision+1`；顺序/并行/混合依赖正确；Child WorkOrder→SubAgentRequest lineage/授权完整；cycle 返回 `PLAN_CYCLE`。
- T-NLE-06：TaskStore 的 CAS、attempt、lease、heartbeat、重启恢复、业务幂等 reservation、budget reservation/usage 和 transactional outbox 可并发验证；Outbox 必须覆盖双进程唯一 claim、expired takeover、stale fence 拒绝、同 `aggregate_type + aggregate_id + graph_revision` 严格顺序、release backoff，以及 append 成功但 complete 前进程死亡后的幂等收敛。新进程在旧 claim 到期前启动时必须由后台续投线程在到期后自动接管；只要 canonical DB 仍有 PENDING，drain 就不得误报 `DRAINED`。完整 PENDING inventory 与 delivery-ready backlog 必须分离，逐条 claim/complete/release 不得重复执行 schema/全表 readiness 扫描。Publisher close 必须等待已进入的前台 publish；event 已提交后的 backlog 读取故障只能降级且不得泄漏底层错误详情或制造假失败。恢复结论必须按窗口标注：Outbox/coordination takeover、single-node post-budget Operation continuation 与 general/batch/pre-budget recovery不是同一能力。
- T-NLE-06A：0094 cancellation continuation 必须覆盖双进程唯一 coordination claim、heartbeat 续租、expected-version/owner/token/fence 拒绝 stale worker、defer deadline、expired takeover、migration receipt-loss replay 与 finalized cleanup；claim/heartbeat/defer/complete 必须在 `BEGIN IMMEDIATE` 取得写锁后才采样时钟，锁竞争不得吞掉 lease 或用旧时间授权 CAS。Worker 必须从 canonical HostCommandRepository 按 exact `graph_id + revision` 重建 command/work order/assignment/graph 与 `AdmittedExecution(replayed=true)`，只调用既有 GraphCancellationRuntime；0092 intent/decision 始终为唯一业务真相。Process container 必须向 Host/Internal/Scheduler Coordinator 与 worker 注入同一 `LocalGraphExecutionRegistry`，但不得将它当 durable authority；Runtime 未显式获得 `local_pending` 时必须 `cancel_and_join`，非空 pending 不得 advance/finalize。operation/effect/review 等事实未收敛时必须 durable defer，不得 complete 或伪报 Graph 已取消。worker start 失败必须尝试反向 stop/join 并 close Coordinator；只有全部回滚成功才 unbind，失败时保留 bound handle 供 `aclose` 重试，Runtime Group 也必须保留关闭失败的 child。shutdown 必须有界 grace+cancel+join，拒绝取消时必须 fail closed 且 Store 保持开放供重试。生产 Operator authority/入口即使已接线也必须默认 OFF；Operation/Effect 签名对账与 0098 历史 inventory/repair/quarantine 虽已接线，外部部署 inventory 仍未知。Budget contention 已由 T-NLE-06F 证明，但历史半状态、general recovery、部署凭据、完整 attestation key lifecycle 与真实 canary 未完成前不得改变 `PRODUCTION_OFF` 定级。跨进程业务证据由 `tests/integration/test_ne01_graph_cancellation_cross_process_continuation.py` 验收。
  同一 T-NLE-06A 还必须验证 0094 readiness/codec 只接受 exact frozen columns，额外 `status` 等业务字段 fail closed；`PRAGMA table_info` 的 name/type/notnull/default/pk 必须逐列精确一致，且 `foreign_key_list` 只能是单一 `id=0`、`seq=0/1/2` 三列复合外键，拆分/额外 FK 一律拒绝。`intent_ref` 必须是 `TEXT NOT NULL PRIMARY KEY`，所有必填 identity/counter/timestamp 列均保持 `NOT NULL`，readiness 必须拒绝 SQLite rowid PK NULL/复合 FK NULL 豁免幽灵行。
  Lifecycle 故障均必须返回 `TASK_RUNTIME_LIFECYCLE_RETRY_REQUIRED`；activation rollback 或 `aclose` quiescence 未完成后必须拒绝 bind/activate，只允许 `aclose` 重试。Coordinator admission close、drain、`aclose` 任一失败都必须保持 Store 开放；只有 Coordinator 和 worker 都已静默才能 drain Outbox/关闭 infrastructure。
- T-NLE-06B：0095 revisioned Graph Outbox migration 必须覆盖 0090/0091/0092 decision、0092 intent、Attempt、唯一 revision、HostCommand evidence 精确 CREATED，以及 deterministic `natsume_sha256_hex` 以 revision-bound command ID 重算 legacy governed `outbox_id + idempotency_key` 后精确恢复 ACTIVE 等非 attempt transition。升级后 ID/key 必须与升级前相同；冲突或不可解析自定义 producer 必须令 `BEGIN IMMEDIATE` 整笔失败。readiness/receipt-loss gate 必须冻结 `graph_revision = INTEGER/notnull0/defaultNULL/pk0`、column check、revision delivery index、Graph revision/payload insert+update trigger、`TASK_STORE_OUTBOX source_ref` unique partial index 与全表 binding。新 Graph payload ref 必须是 `taskstore://event/TASK_GRAPH/{graph_id}@{revision}/{version}`；旧 ref 只允许 durable codec/replay，任何新 insert/update 必须拒绝。所有生产 Graph producer 显式绑定 revision；decision getter 与 multi-revision marker scan 保持 exact。claim/head 同 revision 保序、不同 revision 独立。新 Runtime Event 为 `graph_id@revision`，旧 journal 不改写并经 source Outbox revision 查询；append 成功但 complete 未确认的 legacy 重投必须只在现存 event 与 current/legacy 重建结果全字段一致时返回原 journal，unique source fence 下不得重复投影，漂移必须拒绝。Operator/ReviewStore 必须按 canonical URL-escaped task-node subject ref 精确 revision 过滤。0098 只能把 `GRAPH_CANCELLATION_EFFECT_RECONCILIATION` 加入 revisioned Outbox allowlist；该 aggregate 的 positive `graph_revision` 与 payload ref 必须由 insert/update trigger 物理绑定到 immutable Effect case，签名 binding 与 trusted read 必须复验同一 revision，除 `TASK_GRAPH` 和该 aggregate 外的 Outbox 仍必须保持 revision null。证据至少覆盖 migration compat、RuntimeOutbox、Outbox durable claim、Runtime Event spine/contract、Review/Operator contract 与 cross-process delivery tests。
  同一 T-NLE-06B 还必须验证 `created_ts_ms=0` 不被替换为当前时钟；由于 current revisioned 与 legacy unrevisioned 两条历史 append 路径都可能以时钟替代零值，timestamp 容差适用于 source zero 的两种可重建 candidate，但 event ID/schema/type/source/aggregate/payload/evidence/dedupe/redaction 等其它字段必须 exact，source timestamp 非零时 timestamp 也必须 exact。`read_scheduler_snapshot` 默认不返回/扫描 `graph_outboxes`；PlanningRecovery 必须显式 `include_graph_outboxes=True`，在同一只读事务取得 exact revision inventory，并使用唯一 terminal status/version Outbox 的实际 payload ref。pre-0095 legacy ref 必须原样进入 failure evidence；只有旧版或替代 Store 未提供可选扩展字段时才合成 revisioned fallback，生产 DurableTaskStore 提供 inventory 后，类型错误、缺行或歧义必须 durable drift。证据由 `tests/unit/test_gr04_planning_recovery.py` 与 `tests/integration/test_gr04_planning_replan_sqlite.py` 覆盖。
- T-NLE-06C：中央 Task mutation Outbox insert/replay helper 必须只定义在 `TaskOutboxDeliveryStoreMixin`，`DurableTaskStore._insert_outbox` 与 `_assert_mutation_outbox_replay` 必须保持该 mixin 的 method identity；`task_repository.py` 不得保留重复定义。所有 mutation/replay 调用必须复用 owner transaction 的 `conn`，既有 MRO、CAS、response-loss、legacy/current 与 0095 exact revision 行为测试必须继续通过；`core_architecture_policy` 必须验证 `task_repository.py` 不超过 4,600 行。该结构证据不得被解释为新增 Store、事务或发布 authority。
- T-NLE-06D：`task_graph.cancel` 生产权限/入口必须覆盖以下矩阵：Operator command contract/path 只接受 exact `graph_id + positive graph_revision + canonical reason_code`，并要求 `obs:write + task-runtime:cancel`；Gateway/BFF 生成 server-owned structured authority，Application 拒绝调用方自报 `request_id/request_identity/source`；共享 `SecureIngressGraphCancellationAuthorityVerifier` 必须重读 committed audit/claim，逐项绑定 request/principal/tenant、认证 body digest、action/scopes、path/method 和 allow/complete 状态，任一漂移 fail closed；`claim_result_ref` 非空的 settled authority 必须拒绝复用为 bearer，公开 duplicate 只重放原 outcome。Host/Internal/Scheduler 必须接收同一个 verifier 实例，Runtime Group 按 immutable HostCommand surface 精确路由。下游异常后，无 exact intent 必须明确 `side_effects_may_have_run=false`，匹配本次 request/audit/source/reason/graph/revision 的 intent 才能为 true，读取失败或畸形事实必须保守 uncertain；只有 cancellation handler 显式翻译的 OBS 封闭端口异常可携带确定性，任意依赖异常即使 duck-type 同名字段也必须为 uncertain，`CancelledError` 原样传播。feature flag 默认 false；显式开启而 Secure Ingress 非 `ENFORCE` 或 Gateway 不可用时，ASGI 必须在路由开放前启动失败。UI 必须提交 exact revision，收到命令结果后刷新 durable detail，不得乐观显示 `CANCELLED`。证据至少包括 `tests/integration/test_ne01_graph_cancellation_secure_operator_path.py`、`tests/security/test_ne01_graph_cancellation_operator_authority.py`、`tests/unit/test_ne01_graph_cancellation_operator_routing.py`、`tests/unit/test_obs_domain_command_ports.py`、`tests/unit/test_aw01_asgi_security_composition.py`、`tests/contract/test_aw07_operator_command_gateway.py` 与 Monitor UI operator gateway/client tests。该 Gate 通过只允许定级 `WIRED_DEFAULT_OFF / PRODUCTION_OFF`。
- T-NLE-06E：Cancellation Acceptance Effect 不确定性必须覆盖四类既有合同：取消请求观察到一般 `FAILED` 时 fence 为 `RECONCILIATION_REQUIRED`；`CLAIMED` effect 在取消提交后发生 handler/transport `FAILED` 时仍不得解除 fence；带 durable receipt 的 `COMMITTED` 可成为 `COMMITTED_EVIDENCE` 并允许继续收口；历史 `FAILED_EVIDENCE` 行保持可读但新路径不得生成。测试必须证明 `advance_graph_cancellation()` 不会把 `FAILED` 自动升级为证据，`pending_effect_ids` 保持该 effect，且 status/reason code 不能替代签名 `NO_SIDE_EFFECT` attestation。PENDING classifier 还必须覆盖 exact virgin tuple（attempt_count/fencing_token/status_version 全 0，reason_code/last_error 均空）才可 `CANCELLED`，row-canonical retry tuple的最小及更高单调计数均为 `RECONCILIATION_REQUIRED`，五字段任一混合或弱化组合按 durable corruption fail closed。这里的 retry 分类是纯函数/legacy hardening合同，不代表正常可达竞态：public claim API必须证明未 ACCEPTED NODE effect无法 claim；accepted NODE上的真实 retry PENDING仍保留 acceptance authority、不会产生 cancellation fence，GRAPH effect只有 COMPLETED后才可 claim而该 Graph已终态。Store contract必须用注明为 direct-SQL 的 legacy/imported/cross-table corruption fixture构造 fenceable retry PENDING，并证明 request提交后响应丢失 exact replay、连续两次 advance仍 pending、canonical `taskcancel-effect://sha256/...` evidence ref精确且稳定、direct finalize返回 `TASK_STORE_OWNERSHIP_CONFLICT`。0098 必须把历史 pre-fix `FAILED_EVIDENCE` 与 retry-PENDING + CANCELLED fence收录为 append-only immutable case，按 `REPAIRABLE_REQUESTED`、`QUARANTINED_FINALIZED`、`QUARANTINED_STRUCTURAL` 分类；不得改写 0087 Effect、0092 fence/decision。`PENDING_CANCELLATION` proof 只能进入新 decision evidence；`LEGACY_FINALIZED_CORRECTION` 必须绑定旧 decision ref + payload digest，作为 trusted overlay 且绝不写入旧 evidence；structural quarantine 永远 fail closed。Effect 签名 binding 必须精确覆盖 case、intent、effect、fence、attempt、resolution、authority、Outbox、subject digest 与 positive graph_revision，验证伪签名、未知 key、版本/graph/fence/decision 漂移、response-loss replay、stale CAS；`CLAIMED` 还必须要求 expired lease 与 executor quiescence，`SIDE_EFFECT_CONFIRMED` 必须有 downstream receipt。Operation 与 Effect 对账都只定级 `WIRED_DEFAULT_OFF / CANARY_NOT_RUN`；Budget contention 已由 T-NLE-06F 证明，但外部部署 inventory、历史半状态、general recovery、部署凭据、完整 key lifecycle 与真实 canary 未完成前 durable cancellation 保持 `PRODUCTION_OFF`。证据至少包括 `tests/unit/test_ne01_effect_cancellation_classification.py`、`tests/contract/test_ne01_graph_cancellation_store.py`、`tests/unit/test_ne01_graph_cancellation_operation_reconciliation.py`、`tests/unit/test_ne01_operation_reconciliation_verifier_wiring.py`、`tests/unit/test_ne01_graph_cancellation_effect_reconciliation.py`、`tests/unit/test_ne01_effect_reconciliation_migration.py`、`tests/unit/test_ne01_effect_reconciliation_operator_reads.py`、`tests/integration/test_ne01_graph_cancellation_effect_reconciliation.py` 与 ASGI/OBS composition tests。
  同一 Gate 还必须验证 dedicated Obs 在 views 关闭时不触碰 0098，开启时只使用同 canonical SQLite 的 `mode=ro + query_only=ON` reader，不构造 `DurableTaskStore` 或 UoW。三条 Effect GET 路由必须精确要求 `obs:task-runtime:read`，并拒绝仅有 `obs:operator:read` 的 principal。privacy serializer 后必须保持零非预期 redaction：公开 summary 最小化，exact subject 以 canonical-JSON base64url + sha256 专用 signing material 交付；真实 Ed25519→service→SQLite roundtrip 必须成功且 raw error/signature 不泄漏。证据为 `tests/integration/test_ne01_obs_effect_reconciliation_read.py`、`tests/unit/test_aw07_operator_query_gateway.py` 与 `tests/unit/test_obs_fastapi_app.py`。
- T-NLE-06F：Budget 高竞争必须覆盖 8/16/32 线程及独立进程。只有一个 rejected usage 可以成为 `EXCEEDED` winner、推进 Budget version并拥有唯一 Outbox；其余 sibling必须记录 `ALREADY_EXCEEDED`并引用 winner。0090 Budget decision 写入、读取与 replay 必须在单一 SQLite 读快照中复核 terminal Reservation 精确版本、唯一 winner/Outbox、Attempt/Graph lineage和全部 sibling；篡改 Reservation version、插入第二条同版本 Outbox或改写 sibling authority必须 fail closed。`DurableBudgetRuntime._charge` 的 attempt 数必须为 `max(configured retries, policy concurrency + 1)`；`tests/unit/test_ne01_budget_runtime_contention_retry.py` 必须证明 concurrency=8、连续前8次 `TASK_STORE_CAS_CONFLICT` 后第9次成功。exact replay、restart、interleave、winner/sibling drift和非法 candidate必须 fail closed。该 Gate 仍不证明 concurrency charge、DEGRADE/REVIEW或 program-wide coverage。
- T-NLE-06G：0099 terminal reconciliation 只验收默认关闭的终态历史对账能力，不验收 general recovery 或生产启用。Migration 必须是 schema-only：只安装 immutable append-only case/proof/supersession、索引、约束与 trigger，不得扫描、inventory、backfill、repair 或改写 Graph/Budget/0090/0092 authority。Readiness 必须从受信 0099 migration 资产在隔离 SQLite 中生成基准，对 live `sqlite_master` 严格比较 3 张 table、4 个 index 与 10 个 trigger 的全部 canonical SQL；同名 no-op trigger、错列 index、table CHECK/constraint 漂移或多/少同前缀对象都必须 fail closed。应用层还必须以 Python validator 重验 case/subject/proof 的 exact schema、canonical JSON/digest、列绑定与 authority matrix，不能只信 SQL trigger。显式 inventory 必须排除 ACTIVE/CREATED，只接受五类 anomaly case：`FAILED_UNSETTLED_BUDGET`、`BLOCKED_FAILURE_NODE`、`CANCELLED_UNSETTLED_BUDGET`、`COMPLETED_INCONSISTENT`、`TERMINAL_STRUCTURAL_ANOMALY`；其中 `COMPLETED_INCONSISTENT` 与 `TERMINAL_STRUCTURAL_ANOMALY` 必须归为 `QUARANTINED_STRUCTURAL + NONE` 并永久禁止 repair。`EXACT_REPAIRABLE` 允许 `SYSTEM_DURABLE_EVIDENCE` 或 `SIGNED_OPERATOR`，`SIGNED_REPAIR_REQUIRED` 只允许 `SIGNED_OPERATOR`；`RELEASE_BUDGET`、`FAIL_GRAPH`、`FAIL_GRAPH_AND_RELEASE_BUDGET`、`NONE` 的 target status 与 Graph/Budget Outbox 必须逐项精确。伪签名、未知 key、stale case/subject version、action/target/outbox 漂移必须零副作用 fail closed。一次 proof 必须在同一 Store UoW 只改写 exact terminal Graph/唯一 graph-scope Budget 与 deterministic Outbox；不得创建 Attempt/Lease、重进 worker 或静默 resume，response-loss replay返回同一 proof，并发只能有一个 immutable winner，注入式中途失败必须整笔回滚。独立 Obs 与主 Host ASGI 的 Operator candidates/cases/challenge 都必须显式构造同 canonical SQLite 的 `mode=ro + query_only=ON` reader，不得回退到 DurableTaskStore 或 writable UoW；challenge 必须直接返回最终 Store-verifiable Ed25519 binding bytes，绑定 authenticated query principal、authority key、attestation ref/time、exact case/subject、action/targets 与 deterministic Graph/Budget Outbox，并返回同一 command request，HTTP→Ed25519→service→SQLite proof 必须 round-trip。`HOST_TASK_TERMINAL_RECONCILIATION_OPERATOR_ENABLED` 与 `HOST_TASK_TERMINAL_RECONCILIATION_AUTO_ENABLED` 必须独立默认 false；自动路径只处理 `EXACT_REPAIRABLE`。取消生产 gate 必须同时检查 0098 Effect 与 0099 terminal readiness；remediation Operator 单独启用不得触发或阻断 cancellation production gate，它只受自身 Secure Ingress/Gateway/key 启动条件约束。证据为 `tests/unit/test_ne01_terminal_reconciliation_migration.py`、`tests/unit/test_ne01_terminal_schema_codec_hardening.py`、`tests/unit/test_ne01_terminal_reconciliation_classification.py`、`tests/unit/test_ne01_terminal_reconciliation_service.py`、`tests/unit/test_ne01_terminal_reconciliation_operator_reads.py`、`tests/unit/test_terminal_reconciliation_query_repository.py`、`tests/integration/test_ne01_obs_terminal_reconciliation_read.py`、`tests/integration/test_ne01_terminal_reconciliation_store.py`、`tests/integration/test_ne01_terminal_reconciliation_decision_authority.py`、`tests/unit/test_ne01_operation_reconciliation_verifier_wiring.py`、`tests/unit/test_aw01_asgi_security_composition.py`、`tests/security/test_obs_secure_command_ingress.py`、`tests/unit/test_obs_domain_command_ports.py` 与 `tests/unit/test_obs_fastapi_app.py`。该 Gate 只能定级 `WIRED_DEFAULT_OFF_CANARY_NOT_RUN`；外部 deployment inventory、部署 active/retired/revoked key lifecycle 与真实 canary 为 `UNKNOWN/NOT_RUN`，general/multi-node/pre-budget recovery 仍为 `INCOMPLETE`，durable cancellation 仍为 `OFF`。
  分批 inventory 还必须证明已持久化的前一批不会挡住后一批，重复调用会在 10,000 个 terminal Graph 的显式扫描上限内确定性推进且不重复插入；`FAILED/CANCELLED + PENDING/CONSUMED Budget` 属于不可安全推断的结构异常，必须冻结为 `QUARANTINED_STRUCTURAL + NONE`，不得当作已收敛状态跳过。
  历史 Graph 重入还必须在 Runtime 快照层拒绝为 `BLOCKED/COMPLETED/FAILED/CANCELLED` Graph 创建或激活 Budget，并由 Store 在 `BEGIN IMMEDIATE` 写事务内再次校验 Graph status 仅为 `CREATED/ACTIVE`；测试必须证明拒绝路径不新增/改写 Budget 或 Outbox，同时不阻断终态 Graph 既有 ACTIVE/EXCEEDED Budget 的消费、释放以及无 Graph 绑定的 Budget。该防线只阻止重入制造新的半状态，不代表 pre-budget recovery 已完成。
  Budget authority 必须精确限定为恰好一条 `node_id IS NULL AND attempt_id IS NULL` 的 graph-scope Reservation；缺失、多条或 child-scoped Budget 都必须进入 `QUARANTINED_STRUCTURAL + NONE`，SQL trigger、classifier 与 proof mutation 需分别防御。终态 classifier 与生产 gate 必须使用完整 Graph/Node codec，校验 Review 的 canonical JSON/SQL、subject/revision/phase/attempt binding，终态 Attempt 与已释放 Lease 的 owner/fence/timestamp lineage，以及 0090/0091/0092 canonical parallel terminal decision、Budget after-state 和 live/conflicting authority。0090 必须恰好一条并通过完整 decision/outbox binding，不得从多条中取 latest；0091 必须复用 complete success codec/binding/latest-Attempt validator；0092 必须复用 trusted final-decision 与 Effect overlay validator。Exact Attempt failure bundle 必须同时精确校验 Attempt/Node/Graph/Lease 四条 Outbox 的 aggregate、event、version/revision、evidence/attempt/timestamp、idempotency/outbox ID 与 canonical `payload_ref`；任一漂移都必须零写入 fail closed。Outbox 的投递状态变化不得错误地使 authority snapshot 失效。
  同 Graph/revision 上已 inventory 且仍代表 current state 的未解决 case 必须为 graph-wide 写围栏；不只拒绝 `transition_budget(RELEASED)`，还必须拒绝普通 TaskGraph transition、Graph cancellation request、Budget transition/consume、BudgetUsage journal 与 retry Budget mutation。拒绝路径必须保持 Graph/Budget/Usage/Outbox 零写入；0099 proof mutation 在同一 Store UoW 中是唯一显式 `terminal_reconciliation_bypass`，其他 writer 不得暴露 bypass。后续状态变化必须冻结新的 replacement case，并用 immutable supersession 连接旧 case；旧 case 不得再直接 proof。readiness 必须验证每个 proof 和 supersession 的绑定，并要求所有历史 case 经零条或多条 supersession 最终终止于 trusted proof；断链、环、漂移、未证明的末端、current repairable/signed-required 或 quarantine 都必须阻断生产。
  默认关闭的自动路径必须在查询层只取 `EXACT_REPAIRABLE`，循环消费有界 batch 直至不存在 exact candidate；达到显式总量上限时必须返回 `TRUNCATED` 并报告 `remaining_exact_cases=true`，不得因前缀中的 signed/quarantine case 饿死后续 exact case，也不得伪报完成。`SYSTEM_DURABLE_EVIDENCE` 请求的 `issued_ts_ms` 必须取 immutable case `created_ts_ms`，使 request/proof ID 对重启和多进程时钟稳定；两个进程对同 exact case 必须收敛到同一 proof 或 exact replay，不能让 loser 因启动时钟不同而变成不确定错误。批处理若在前缀 case 已提交后读取后续 candidate、应用后续 case 或验证 completion 失败，异常必须带已提交数并且 `side_effects_may_have_run=true`；尚无任何 result 时才可为 false。一次 startup inventory 必须覆盖受支持的 10,000 个 terminal Graph 上限；该边界不是外部 deployment 已完成盘点的证明。
  上述五类是 anomaly case；第六类 `TERMINAL_HEALTHY_CLOSURE` 仅可在同 Graph/revision 存在 unresolved unsuperseded 历史 case 且完整 current classifier 当前返回 healthy 时冻结为 `SIGNED_REPAIR_REQUIRED + NONE`。它必须以 immutable supersession 接续旧 case，且只能由 `SIGNED_OPERATOR` proof 闭链；`SYSTEM_DURABLE_EVIDENCE` 对 healthy closure 必须被 SQL 与 Python 双层拒绝。Proof 必须固定 `target_graph_status=null + target_budget_status=null + outboxes=empty`，不得修改 Graph/Budget 或写 Outbox；healthy snapshot 再漂移必须继续追加 replacement + supersession，不得对 stale closure 直接 proof。`QUARANTINED_STRUCTURAL` 本身永远不可直接 proof。
- T-NLE-06H：Operation 自动 continuation 只验收 single-node post-budget checkpoint。Store 必须一次证明 exact COMMITTED Reservation、唯一 `OPERATION_COMMITTED` Outbox、原 Attempt、历史 Lease/owner/fence、hash receipt；runtime 再证明 canonical WorkerProjection完整 lineage和恰好一条 accepted worker BudgetUsage。旧 Lease 到期后新 owner/fence接管同一 Attempt，worker调用数保持 0，只继续 Verification/Acceptance。COMMIT 后缺 BudgetUsage、pre-budget、batch/multi-node、public/noncanonical projection或证据漂移必须在取得新 Lease/完成 Graph前 fail closed。另行覆盖 Node ACCEPTED/Graph ACTIVE/Effect空的进程退出：共享 finalizer重启后原子补写同一 Effect，重复 replay不新增 Effect，篡改 projection时 Graph不完成。证据为 `tests/contract/test_ne01_operation_checkpoint_store.py`、`tests/integration/test_ne01_operation_online_takeover.py`、`tests/unit/test_ne01_operation_recovery_runtime.py` 与 `tests/integration/test_ne01_accepted_effect_finalization_recovery.py`。该 Gate 只能定级 `SINGLE_NODE_POST_BUDGET_CHECKPOINT_PROVEN / GENERAL_RECOVERY_INCOMPLETE`。
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
