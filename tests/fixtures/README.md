受众：CODEX
标题：Fixtures 占位

目录说明：
1. `intent/`：good/bad intent 样例。
2. `llm/`：good/bad LLM 输出样例。
3. `replay_logs/`：回放日志样例。
4. `cold_start/`：无 API key/外部依赖不可用样例。
5. `prompt_context/`：分槽隔离与 Canon 引用口径样例。
6. `governor_output/`：GovernorOutput 契约正反例（reason_code/token/rule_trace）。
7. `tokens/`：approval/confirm/undo 绑定、过期、伪造样例。
8. `governor_rules/`：R1~R7 gate 快照样例（命中/未命中）。
9. `intent/proposals/`：多 proposer 元信息样例（A/B）。
10. `canon/`：style/world canon 的正反例与版本漂移样例。
11. `memory/`：episodic 写入门禁与生命周期（merge/decay/delete）样例。
12. `vault/`：manifest/undo/GC 的正反例与 digest 失配样例。

注意：fixtures 仅提供样例结构，不代表真实执行成功。
13. `airlock/`: egress sanitization, ingress quarantine fixtures (missing report, direct-execution ingress sample, fact sample), and DRAFT_CODE sandbox execution fixtures (normal exec, timeout, malicious code, resource exceeded for `T-Sandbox-01`).
14. `tools/`: tool catalog metadata fixtures (mixed READ/WRITE and metadata-only contract negatives).
15. `mcp/`: provider tool sync fixtures (READ/WRITE classification, metadata validation, and governed invocation paths).
16. `channels/`: WeChat ingress/egress and VTS avatar fixtures (duplicate ingress, failed egress ack, invalid emotion tags, Host-side `DEGRADED + neutral` fallback, Host/Edge 16-tag mapping alignment, and free-form action blocking).
17. `vision/`: vision fixtures for ClawdBot UI guardrails, screen OCR contracts, camera recognition events (gaming gate blocks, structured OCR output, evidence_ref replay linkage, low-confidence marking), and multi-detector aggregation (mixed all-OK, partial-unavailable, all-failed scenarios with per-detector evidence_ref and stable degradation classification, `T-Vision-03`).
18. `services/`: service unavailable matrix fixtures for registry/health degradation reason_code mapping and request-level audit logging, nine-node deployment fixtures (`node_id/region/role/services/health_endpoint/key_profile_id`) for missing-service/role-conflict/probe-timeout scenarios (`T-Infra-01`), and environment-readiness integration fixtures covering partial-ready and critical-down dependency baselines (`T-EnvReady-01`).
19. `voice/`: Edge voice RPC fixtures for `asr_chunk/speak/barge_in` and resource-gate pressure scenarios (priority/mutex with vram/queue metrics).
20. `retrieval/`: embedding/rerank/evidence-pack fixtures for stable retrieval contract outputs, degrade reason_code mapping, retrieval_event_log audit linkage, Chroma external config/probe plus collection governance failures (`endpoint_down/collection_missing/vector_dim_mismatch`, `T-Chroma-01`), embedding multi-provider routing config with timeout/unavailable/dim-drift cases (`T-Embed-01`), and reranker routing plus bad-score fallback cases (`model_down/timeout/invalid_score_range`, `T-Rerank-01`).
21. `bond/`: Bond fixtures for SSOT single-writer updates, summary derivation/injection (`event_id/evidence_refs` replay refs), Neo4j external config/probe failures (`auth/schema/leader`) with migration-linkage assertions, missing-evidence rejection, and static-scan non-single-writer violations (`T-50~T-54`, `T-Neo4j-01`, `T-74~T-75`, `T-SSOT-01`).
22. `config/`: Host/Edge layered config fixtures for cold-start defaults, invalid env/value/required-key rejection mapping, key-profile `secret_ref` resolution (missing/scope-mismatch/expired), env snapshot redaction/diff/audit replay rows, and deterministic runtime-status bridging (`T-Config-01`, `T-Config-02`, `T-Key-01`).
23. `llm_gateway/`: gateway fixtures for sanitized egress payload, provider draft responses, format drift, and provider-unavailable degrade paths with AIRLOCK + QA enforcement.
24. `orchestration/`: graph runtime fixtures for `graph_id/version/entry_node/node_bindings/edges/fallback_edges`, including missing-handler, invalid-edge, timeout fallback, and dependency-down fallback scenarios (`T-Orch-01`).
25. `migrations/`: SQLite/Bond migration fixtures for idempotent apply, missing catalog gap detection, schema drift, and rollback guard scenarios (`T-Migrate-01`).
26. `ci/`: delivery gate fixtures for doc audience/title lint failures and static-scan non-single-writer violations (`T-CI-01`).
27. `release/`: release checklist and rollback map fixtures for `M0~M3` deployable assets, checksum drift/missing asset negatives, and rollback target resolution (`T-Release-01`).
28. `drill/`: production drill fixtures for `cold_start_no_key`, `provider_down`, and `rollback_conflict`, with deterministic scenario report fields (`scenario_id/status/reason_code_chain/replay_ref`) and failure classification/disposition (`T-Drill-01`).
29. `emotion/`: emotion closed-loop regression corpus for mood-tag constraints, intensity clamping, prompt binding consistency, and VTS expression mapping (`T-Emotion-Regression-01`).
