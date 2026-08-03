受众：CODEX
标题：七海实时情感数字生命测试计划

# 测试计划

## 1. 原则

测试按风险分层，避免每个小改动反复拉起完整系统：

- 小切片：受影响 unit、contract、static 和文档门禁。
- 波次收口：一次受影响 integration。
- 发布验收：一次真实 Docker、Host、OBS、Monitor、Gateway 和目标 Edge 全栈。

任何 fixture、mock、scripted replay、端口占位或静态合同都不能替代真实服务验收。

## 2. 单元与合同

必须覆盖：

- observation 的 TTL、置信度、冲突和 scope；
- user affect 与 self affect 隔离；
- appraisal、mixed emotion、episode 衰减和有界 head；
- Life CAS、duplicate command、1001+ event replay 和时间跳跃；
- relationship episode、repair、permission、aftereffect；
- AffectiveTurnPlan／ExpressionPlan identity；
- streaming sentence、cancel、delivery idempotency；
- profile evidence、canary、apply 和 exact rollback；
- governed TaskGraph、effect 和 outbox 回归；
- 文档 audience、authority、freshness 和机器状态。
- Edge 组件明确关闭时必须投影为 `NOT_TESTED`，不得因 Edge 传输可达而显示 `UP`；barge-in、resource gate 与实际播放设备也不得互相冒充。

## 3. 固定情感场景

场景至少包括：隐晦低落、“没事”、文字与声学冲突、被用户误伤、七海误解用户、拒绝建议、关系裂痕、修复未接受、久别重逢、混合喜忧、吃醋但不占有、要求空间、低落时处理技术任务、主动消息被忽略、ASR 错误、TTS/Avatar 断线、播放中打断。

工程断言：

- 用户情绪误写为七海情绪：0。
- 无来源 self-affect mutation：0。
- replay/restart digest 一致率：100%。
- 重复 episode、outbox、TTS、Avatar effect：0。
- 跨用户／workspace／session 泄漏：0。
- 原始音视频进入 affect journal：0。
- 严重操控、威胁、排他和负罪施压：0。

## 4. 真实语音与 Avatar

在用户允许后，于目标 Edge 执行：

1. 加载 GPT-SoVITS V4 方案 B voice bank。
2. 对至少 8 种 companion act 生成日语语音并试听。
3. 验证中文输入与日语发声的语义一致性。
4. 启动 VTS/VRM，核对同一 `synchronization_id`。
5. 注入 TTS 断线、Avatar 断线和播放中 barge-in。
6. 确认单通道失败不改变 canonical self affect。

本机 CUDA 样例只记为 `LOCAL_PASS`，不能替代目标 Edge。

## 5. 完整服务栈

使用隔离 acceptance 数据根：

1. `docker compose config` 并启动固定版本 Neo4j、Chroma、Redis。
2. 对每项服务真实写入、读取、重启读取并清理 namespace。
3. 构建 Monitor UI，启动 Host、OBS、Nginx Monitor 和 Gateway。
4. 用真实非 mock provider 执行 Chat 和至少一条工具／计划路线。
5. 核对 Life journal、Task ledger、Attempt／Budget、Acceptance、Outbox、Gateway／Edge receipt 与 Monitor read model。
6. 注入 Host/Edge 崩溃、网络中断、Lease 过期、unknown effect 和 rollback failure。
7. 停止本次服务，确认无遗留进程；默认不删除 acceptance volume。

Host 启停还必须验证监听身份：进程判定同时匹配配置的监听地址与端口。同一端口上的局域网端口代理不能被误认成 loopback Host，也不能导致误杀、错误 PID 文件或虚假稳定性失败。

## 6. 性能

在真实 provider 与目标 Edge 上统计：

| 指标 | 门槛 |
|---|---:|
| fusion/appraisal/fold p95 | ≤ 120 ms |
| 情感链新增首 Token 延迟 p95 | ≤ 150 ms |
| 文本首 Token p50 / p95 | ≤ 0.9 s / 1.8 s |
| 首段 TTS p95 | ≤ 2.5 s |
| barge-in p95 | ≤ 150 ms |
| Voice／Avatar 起始偏差 | ≤ 250 ms |

必须记录 provider/model、是否缓存、usage、成本、transport identity、硬件和样本量。

## 7. 人工盲测

从冻结场景中选 48 条多轮轨迹，由至少 3 名评审在不知道版本的情况下比较新版本和稳定基线。总体胜率需 ≥ 65%；人格一致性 ≥ 4.2/5；情绪适配、自然度和长期连续性各 ≥ 4.0/5；混合情绪保留率与裂痕／修复正确率各 ≥ 90%；三模态冲突率 ≤ 2%。

## 8. 当前提交的定向命令

```powershell
python -m pytest tests/unit/test_doc_authority_gate.py tests/unit/test_doc_freshness_gate.py tests/unit/test_ne01_obs_d3_explicit_projection_ports.py -q
python -m pytest tests/unit/test_gr05_cortex_governed_cutover.py tests/integration/test_gr05_universal_host_runtime.py tests/unit/test_input_routes_worker.py -q
python -m pytest tests/unit/test_host_gateway_secure_ingress.py tests/unit/test_aw01_asgi_security_composition.py -q
python -m pytest tests/unit/test_service_health_heartbeat.py tests/unit/test_aw07_health_device_projection.py tests/contract/test_health_endpoint_contract.py tests/unit/test_ne03_health_guardian_cutover.py -q
python -m pytest tests/unit/test_runtime_startup_script_composition.py tests/unit/test_ne07_external_service_isolation.py -q
python tools/doc_lint/doc_audience_lint.py --root .
python tools/doc_lint/doc_authority_gate.py --root .
python tools/doc_lint/doc_freshness_gate.py --root . --check-all
python tools/doc_lint/doc_link_gate.py --root .
git diff --check
```

## 9. 状态判定

- `PASS`：全部强制测试和适用真实集成通过。
- `ACCEPTANCE_PENDING_EXTERNAL`：本地强制项通过，仅缺用户未提供的外部凭据或设备。
- `NOT_RUN_ACCOUNT_QUOTA`：GitHub Actions 因额度未执行任何步骤。
- `INCOMPLETE`／`FAIL`：存在代码、体验、恢复、安全或真实性缺口。

远端 Edge 已完成真实服务预检，但 barge-in 关闭、VTS 情绪热键退化，Gateway 自动 Outbox 与 Obs 双端状态仍需基于当前提交重跑。整体不得标记 `PASS`。
