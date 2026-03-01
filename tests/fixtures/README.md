受众：CODEX
标题：Fixtures 占位

目录说明：
1. `intent/`：good/bad intent 样例。
2. `llm/`：good/bad LLM 输出样例。
3. `replay_logs/`：回放日志样例。
4. `cold_start/`：无 API key/外部依赖不可用样例。
5. `context_pack/`：分槽隔离与 Canon 引用口径样例。
6. `governor_output/`：GovernorOutput 契约正反例（reason_code/token/rule_trace）。
7. `tokens/`：approval/confirm/undo 绑定、过期、伪造样例。
8. `governor_rules/`：R1~R7 gate 快照样例（命中/未命中）。
9. `intent/proposals/`：多 proposer 元信息样例（A/B）。
10. `canon/`：style/world canon 的正反例与版本漂移样例。
11. `memory/`：episodic 写入门禁与生命周期（merge/decay/delete）样例。
12. `vault/`：manifest/undo/GC 的正反例与 digest 失配样例。

注意：fixtures 仅提供样例结构，不代表真实执行成功。
13. `airlock/`: egress sanitization and ingress quarantine fixtures (missing report, direct-execution ingress sample, fact sample).
14. `tools/`: tool catalog metadata fixtures (mixed READ/WRITE and metadata-only contract negatives).
15. `mcp/`: provider tool sync fixtures (READ/WRITE classification, metadata validation, and governed invocation paths).
16. `channels/`: WeChat ingress/egress and VTS avatar fixtures (duplicate ingress, failed egress ack, invalid emotion tags, and free-form action blocking).
17. `vision/`: vision fixtures for ClawdBot UI guardrails, screen OCR contracts, and camera recognition events (gaming gate blocks, structured OCR output, evidence_ref replay linkage, and low-confidence marking).
18. `services/`: service unavailable matrix fixtures for registry/health degradation reason_code mapping and request-level audit logging.
19. `voice/`: Edge voice RPC fixtures for `asr_chunk/speak/barge_in`, fallback LLM contracts (`presence/asr_compile/retrieval_hints/context_compress`), and resource-gate pressure scenarios (priority/mutex with vram/queue metrics).
20. `retrieval/`: embedding/rerank/evidence-pack fixtures for stable retrieval contract outputs, degrade reason_code mapping, and retrieval_event_log audit linkage.
21. `bond/`: Bond fixtures for SSOT single-writer updates, summary derivation/injection (`event_id/evidence_refs` replay refs), missing-evidence rejection, and static-scan non-single-writer violations (`T-50~T-54`, `T-74~T-75`, `T-SSOT-01`).
22. `config/`: Host/Edge layered config fixtures for cold-start defaults, invalid env/value/required-key rejection mapping, and deterministic runtime-status to service-health bridging (`T-Config-01`).
23. `llm_gateway/`: gateway fixtures for sanitized egress payload, provider draft responses, format drift, and provider-unavailable degrade paths with AIRLOCK + QA enforcement.
24. `migrations/`: SQLite/Bond migration fixtures for idempotent apply, missing catalog gap detection, schema drift, and rollback guard scenarios (`T-Migrate-01`).
25. `ci/`: delivery gate fixtures for doc audience/title lint failures and static-scan non-single-writer violations (`T-CI-01`).
