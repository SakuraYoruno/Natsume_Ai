受众：MASTER
标题：全任务风险点汇总（Risk Registry）

# Natsume Ai 全任务风险点汇总（Risk Registry）

版本：v1.0
日期：2026-03-02
范围：TASK-0065 ~ TASK-0075（DevTasks 最终批次 R-31 ~ R-41）

---

## 总览

本文档汇总所有已完成开发任务中识别的风险点，按模块域分类。每条风险标注：
- **来源**：所属 TASK 编号与 Trace 条目
- **风险等级**：P0（阻断性）/ P1（需跟进）/ P2（已知边界/可接受）
- **缓解措施**：当前已采取的对策
- **后续行动**：需要进一步处理的事项

---

## 1. HTTP 服务框架（TASK-0065 / R-31）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R31-1 | FastAPI 应用当前使用 `IntentDecisionEngine` 直接编排，尚未接入真实 LLM Provider，POST /v1/chat 在无 LLM 时返回降级输出 | P2 | 降级路径已覆盖（T-HTTP-02），输出结构稳定 | 接入真实 LLM 后需端到端验证 |
| R31-2 | 中间件层异常兜底返回固定 JSON 结构，极端 OOM 场景可能无法保证 | P2 | 已有 try/except 兜底 + T-HTTP-02 测试 | 生产环境需配合进程管理器（如 systemd/supervisor）守护 |

---

## 2. 链路可视化面板（TASK-0066 / R-32）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R32-1 | 前端面板为纯 HTML+CSS+JS 单页面，嵌入 FastAPI static files；未使用前端框架，大数据量渲染可能有性能问题 | P2 | 面板纯只读，无执行入口（T-UI-01 覆盖安全性） | 数据量增长后考虑分页/虚拟滚动优化 |
| R32-2 | API 数据直接从 SQLite 读取，高并发查询可能造成审计写入阻塞 | P1 | SQLite WAL 模式已优化读写分离（TASK-0070） | 考虑引入只读副本或缓存层 |

---

## 3. Edge ASR 设备集成（TASK-0067 / R-33）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R33-1 | faster-whisper large-v3 模型加载需要 GPU VRAM（≥4GB），CUDA 不可用时自动降级到 CPU 推理，延迟会显著增加 | P1 | WhisperModelManager 实现 CUDA→CPU 自动降级 + `ASR_GPU_OOM` 错误码可统计 | 需在目标 Edge 设备上实测 CPU 延迟是否可接受 |
| R33-2 | sounddevice 麦克风采集依赖系统音频驱动，驱动异常时 `ASR_DEVICE_ERROR` 可稳定报告但无法自动恢复 | P1 | `ASR_DEVICE_ERROR` / `ASR_STREAM_INTERRUPTED` 错误码已定义，健康快照可观测 | 需添加设备重连/自动恢复策略 |
| R33-3 | VAD 静音检测阈值为固定值，环境噪声不同时可能需要动态调整 | P2 | `ASR_VAD_NO_SPEECH` 可统计 | 后续可引入自适应 VAD 阈值 |

---

## 4. Edge TTS/VTS 设备集成（TASK-0068 / R-34）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R34-1 | GPT-SoVITS 模型 GPU 常驻占用 VRAM，与 ASR 模型叠加时存在 GPU OOM 风险 | P1 | 资源闸门优先级（ASR>播放>TTS>兜底LLM）已定义，`TTS_GPU_OOM` 可报告 | 需在实际 Edge 设备上验证 VRAM 分配策略 |
| R34-2 | VTubeStudioDriver 使用 WebSocket 长连接，网络抖动时可能断连；断连降级队列缓存有限 | P1 | 断连时降级到队列缓存，`VTS_CONNECTION_FAILED` 可统计，barge-in 恢复 `reset_to_neutral` 已实现 | 需添加 WebSocket 自动重连机制 |
| R34-3 | VTS 表情映射为 Host/Edge 双端维护的静态表，版本不一致时可能导致未知 expression_id | P2 | T-VTS-HW-01 已验证 Edge/Host 表情映射表一致性 | 版本升级时需同步更新双端映射表并回归 |
| R34-4 | `TTS_SYNTH_TIMEOUT` 超时值为固定配置，不同文本长度下合成耗时差异大 | P2 | 超时可配置，`TTS_SYNTH_TIMEOUT` 可统计 | 后续可引入基于文本长度的动态超时 |

---

## 5. graph_runtime 拆分重构（TASK-0069 / R-35）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R35-1 | 拆分后 5 个模块间通过导入耦合，循环引用风险存在（graph_contract 为共享类型层） | P2 | 所有导入为单向：runtime→executor/fallback/model_resolve→contract；T-Orch-Refactor-01 验证了无循环 | 新增模块时需保持导入方向一致 |
| R35-2 | 对外接口签名完全不变，但内部调用路径变化可能在极端并发场景下暴露竞态条件 | P2 | T-Orch-01 回归 13 项全部通过 | 生产压测需覆盖并发图执行场景 |

---

## 6. SQLite Ledger WAL 优化（TASK-0070 / R-36）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R36-1 | WAL 模式在某些 NFS/网络文件系统上不支持，可能激活失败 | P1 | 激活失败时自动降级到 DELETE 模式 + `AUDIT_WAL_FAILED` 可观测 | 部署文档中需标注存储介质要求（本地磁盘优先） |
| R36-2 | `busy_timeout=5000ms` 在极端写入负载下可能仍不足，导致 `database is locked` | P2 | 写入层已添加 3 次重试策略 + threading.Lock 保护 | 可根据基准测试报告调整 busy_timeout |
| R36-3 | WAL 文件持续增长，依赖 `checkpoint` 回收；自动 checkpoint 阈值为 1000 页 | P2 | close() 自动执行 PASSIVE checkpoint；手动 checkpoint API 已提供 | 长期运行需监控 WAL 文件大小并设置定时 checkpoint |

---

## 7. 微信→Governor→VTS/语音 E2E 测试（TASK-0071 / R-37）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R37-1 | E2E 测试中 VTS/语音降级记录通过直接调用 `ledger.log_avatar_action()` / `ledger.log_voice_event()` 模拟，而非真实 Edge 端上报 | P1 | 这是 Host 层 E2E 的设计边界；TASK-0072 跨端时间线已补充 Edge 事件结构 | 需在真实 Edge 环境中进行端到端集成验证 |
| R37-2 | 降级路径中 `status=DEGRADED` 不是 Host VTS 通道模块（`vts.py`）原生支持的状态值（原生只有 `APPLIED`/`REJECTED`） | P2 | E2E 测试直接写入审计，不经过 `resolve_vts_expression`，避免模块耦合 | 后续如需 Host 端 DEGRADED 状态支持，需扩展 `vts.py` |

---

## 8. 跨端事件时间线（TASK-0072 / R-38）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R38-1 | Edge 事件当前通过 `edge_events` 列表参数传入，尚未实现真实的 Edge→Host RPC 上报通道 | P1 | `EdgeEventReport` 数据结构已定义好，可直接对接 RPC | 需在 `edge_client/rpc/` 中增加事件批量上报接口 |
| R38-2 | 时间漂移检测采用 Host/Edge 时间戳范围对比策略，对极端场景（如 Edge 仅有一条事件）可能不够精准 | P2 | 默认容忍阈值 5 秒可配置；超限时 `TIMELINE_CLOCK_DRIFT_DETECTED` 可观测 | 后续可引入 NTP 时钟同步参考基线 |
| R38-3 | Host 审计表遍历使用内存属性（如 `ledger.voice_event_log`），数据量大时可能有性能影响 | P1 | 当前测试数据量较小未暴露问题 | 后续需增加按时间窗口过滤的 SQL 查询优化 |

---

## 9. Rate Limit 可插拔存储（TASK-0073 / R-39）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R39-1 | Redis 后端（`RedisRateLimitStore`）依赖 `redis` Python 包，当前测试环境无 Redis 实例 | P1 | `DegradingRateLimitStore` 降级兜底；Redis 不可用时自动降级到 InMemory + `RATE_LIMIT_BACKEND_UNAVAILABLE` 可统计 | 需在有 Redis 实例的环境进行集成验证 |
| R39-2 | `GateSnapshot` 增加了 4 个可选字段，调用方如果使用 positional args 构造可能受影响 | P2 | 当前所有调用均通过 `_normalize_snapshot(dict)` 间接构造，无 positional args 风险 | 新增调用方需遵循 dict 构造模式 |
| R39-3 | SQLite Rate Limit 表与审计 ledger 使用不同数据库连接，数据不共享 | P2 | 默认 `:memory:` 各自独立；可通过 `db_path` 配置指向同一文件 | 生产部署时需评估是否共享审计数据库 |

---

## 10. Camera 多检测器注册（TASK-0074 / R-40）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R40-1 | `detect_multi()` 是新增方法，不影响现有 `detect()` 调用方；但多检测器并行执行时线程池竞争可能在高负载下造成延迟抖动 | P2 | `detector_registry.py` 是纯新文件，不改变既有执行路径；T-Vision-03 已验证并行执行 | 生产环境需配置线程池大小上限 |
| R40-2 | 多检测器聚合结果为 `PARTIAL` 时，语义上某些检测器不可用但仍有结果——消费方需正确处理部分降级语义 | P2 | `failed_detectors` 列表与独立 `reason_code` 可供消费方判断 | 需在编排层明确 PARTIAL 结果的处理策略 |

---

## 11. Airlock DRAFT_CODE 沙箱执行（TASK-0075 / R-41）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R41-1 | `subprocess` 执行在 Windows 上的超时行为可能与 Linux 略有差异（信号处理不同） | P1 | 通过 `subprocess.TimeoutExpired` 处理超时；T-Sandbox-01 在 Windows 上全部通过 | 需在 Linux 部署环境中回归验证 |
| R41-2 | 沙箱隔离当前依赖临时目录 + 环境变量标记，并非真正的容器/namespace 隔离，恶意代码仍可访问主机文件系统（只读） | P1 | 生产级沙箱应使用 Docker/nsjail 等容器隔离；当前为 Host 端开发阶段的最小隔离 | 生产部署前需升级为容器级隔离方案 |
| R41-3 | `sandbox_exec_log` 审计表的 `stdout_preview`/`stderr_preview` 截断到 256 字节，可能丢失关键调试信息 | P2 | 完整输出在 `SandboxExecResult` 中可获取；审计表仅存预览 | 如需审计完整输出，可增加 evidence_ref 指向完整日志文件 |
| R41-4 | 沙箱执行器当前仅支持 Python 语言，其他语言返回 `SANDBOX_UNAVAILABLE` | P2 | 不支持的语言稳定拒绝 + reason_code 可统计 | 后续按需扩展 JavaScript/Shell 等语言支持 |

---

## 风险等级统计

| 等级 | 数量 | 说明 |
|------|------|------|
| **P0** | 0 | 无阻断性风险 |
| **P1** | 11 | 需跟进：主要集中在 Edge 真实设备集成、Redis/WebSocket 外部依赖验证、跨平台行为差异、沙箱隔离级别 |
| **P2** | 16 | 已知边界/可接受：性能优化、配置灵活性、极端场景处理 |

---

## P1 风险按优先级排序（建议跟进顺序）

| 优先级 | 风险编号 | 领域 | 摘要 | 建议行动 |
|--------|---------|------|------|---------|
| 1 | R41-2 | 安全 | 沙箱非容器隔离，恶意代码可读主机文件 | 生产部署前升级为 Docker/nsjail 容器级隔离 |
| 2 | R41-1 | 跨平台 | subprocess 超时在 Windows/Linux 行为差异 | 在 Linux 部署环境中回归验证 |
| 3 | R33-1 | Edge GPU | ASR CPU 降级延迟可能不可接受 | 在目标 Edge 设备上实测 CPU 推理延迟 |
| 4 | R34-1 | Edge GPU | ASR+TTS 叠加 GPU OOM | 在实际 Edge 设备上验证 VRAM 分配策略 |
| 5 | R33-2 | Edge 音频 | 麦克风驱动异常无自动恢复 | 添加设备重连/自动恢复策略 |
| 6 | R34-2 | Edge 网络 | VTS WebSocket 断连无自动重连 | 添加 WebSocket 自动重连机制 |
| 7 | R38-1 | 跨端集成 | Edge→Host RPC 上报通道未实现 | 在 edge_client/rpc/ 增加事件批量上报接口 |
| 8 | R37-1 | E2E 覆盖 | E2E 测试模拟 Edge 事件，非真实 RPC | 在真实 Edge 环境中端到端集成验证 |
| 9 | R38-3 | 性能 | 审计表内存遍历大数据量性能差 | 增加按时间窗口过滤的 SQL 查询 |
| 10 | R39-1 | 外部依赖 | Redis 后端未在真实 Redis 环境验证 | 在有 Redis 实例的环境进行集成测试 |
| 11 | R36-1 | 存储兼容 | WAL 模式在 NFS 上不支持 | 部署文档标注存储介质要求 |
| 12 | R32-2 | 性能 | 面板查询可能阻塞审计写入 | 考虑引入只读副本或缓存层 |

---

## 跨任务风险关联（系统性风险聚类）

### 聚类 A：Edge 设备资源竞争（R33-1, R34-1, R34-2）
- **描述**：ASR/TTS/VTS 三个 GPU 服务在 Edge 设备上共享有限 VRAM，叠加峰值可能导致级联 OOM。
- **缓解**：资源闸门优先级已定义（ASR>播放>TTS>兜底LLM），但未在真实设备上验证。
- **建议**：在目标 Edge 设备上进行压力测试，建立 VRAM 分配基线并动态调整。

### 聚类 B：Edge→Host 集成缺口（R37-1, R38-1）
- **描述**：当前 Host 层 E2E 测试和跨端时间线均通过模拟 Edge 事件验证，未经过真实 RPC 通道。
- **缓解**：EdgeEventReport 和 VoiceRpcContract 数据结构已定义好，可直接对接。
- **建议**：实现 edge_client/rpc 事件批量上报后，补充端到端集成测试。

### 聚类 C：SQLite 性能天花板（R32-2, R36-2, R38-3）
- **描述**：审计、面板查询、时间线聚合均依赖 SQLite 单文件数据库，并发读写与数据增长可能成为瓶颈。
- **缓解**：WAL 模式 + busy_timeout + 重试策略已优化基本场景。
- **建议**：中期考虑引入 PostgreSQL 或只读 SQLite 副本，面板和时间线查询走只读连接。

### 聚类 D：沙箱安全级别（R41-1, R41-2）
- **描述**：DRAFT_CODE 沙箱当前为 subprocess + 临时目录级隔离，非生产级容器隔离。
- **缓解**：仅在 Host 开发阶段使用；执行结果不影响主链路状态。
- **建议**：生产部署前必须升级为 Docker/nsjail 容器级隔离，并添加 seccomp/AppArmor 配置。

---

## 历史说明

TASK-0065~0070 的风险点从 requirements_trace.md 的进展记录与代码审查中推断。
TASK-0071~0075 的风险点从各任务交付包的 D) 风险点与回滚策略 章节直接提取。
