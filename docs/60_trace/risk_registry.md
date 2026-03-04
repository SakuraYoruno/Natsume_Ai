受众：MASTER
标题：全任务风险点汇总（Risk Registry）

# Nastume Ai 全任务风险点汇总（Risk Registry）

版本：v3.0
日期：2026-03-03
范围：TASK-0065 ~ TASK-0120（DevTasks 全批次 R-31 ~ R-91，含 M8/M9/M10-A）

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

## 风险等级统计（TASK-0065~0075 批次）

| 等级 | 数量 | 说明 |
|------|------|------|
| **P0** | 0 | 无阻断性风险 |
| **P1** | 11 | 需跟进：主要集中在 Edge 真实设备集成、Redis/WebSocket 外部依赖验证、跨平台行为差异、沙箱隔离级别 |
| **P2** | 16 | 已知边界/可接受：性能优化、配置灵活性、极端场景处理 |

---

## P1 风险按优先级排序（TASK-0065~0075 批次，建议跟进顺序）

| 优先级 | 风险编号 | 领域 | 摘要 | 建议行动 |
|--------|---------|------|------|----------|
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

## 跨任务风险关联（TASK-0065~0075 批次，系统性风险聚类）

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
TASK-0076~0083 的风险点从 DevTasks.md 阶段 8 任务描述与代码审查报告 F-01~F-20 发现项中提取。

---

## 12. 代码质量微调（TASK-0076 / F-13~F-16, F-06, F-12）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R76-1 | `main.py:908` 裸 `print()` 调用残留，生产环境下无法通过日志框架统一管控（F-13） | P2 | 已识别，替换为 `logger.info()` 即可 | TASK-0076 执行修复 |
| R76-2 | `graph_executor.py:43` `compile_runtime_graph` 缺少返回类型标注（F-14） | P2 | 不影响运行时行为；仅类型提示缺失 | TASK-0076 补充 `-> None` 或具体类型 |
| R76-3 | `fastapi_app.py:582,605` 两个 async handler 缺少返回类型标注（F-15） | P2 | 不影响运行时行为 | TASK-0076 补充类型标注 |
| R76-4 | `sqlite_ledger.py._fetch_all()` 使用 `f"SELECT * FROM {table}"` 表名 f-string 插值，虽来自内部常量但存在潜在 SQL 注入风险（F-16） | P2 | 表名来源为内部常量，非用户输入 | TASK-0076 增加表名白名单校验 |
| R76-5 | `sqlite_ledger.py._table_has_column()` 每次调用都执行 `PRAGMA table_info`，频繁调用有性能影响（F-06） | P2 | 当前调用频率尚可接受 | TASK-0076 增加 `_column_cache` 缓存 |
| R76-6 | `requirements.txt` 缺少 `redis` 可选依赖说明，部署者可能不知需单独安装（F-12） | P2 | 运行时 import 失败触发降级，不阻塞 | TASK-0076 补充注释说明 |

---

## 13. 沙箱安全升级（TASK-0077 / R41-1, R41-2）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R77-1 | Docker 容器级隔离后端在 Windows 上不可用（Docker Desktop 限制），仅 Linux 原生支持 | P1 | 自动检测平台，Windows 降级到 subprocess 后端 + `SANDBOX_CONTAINER_UNAVAILABLE` reason_code | 生产环境统一 Linux 部署 |
| R77-2 | Docker 镜像构建依赖网络（拉取基础镜像），离线环境可能失败 | P2 | 支持预构建镜像本地加载 + 构建失败降级到 subprocess | 部署文档标注镜像预拉取流程 |
| R77-3 | nsjail 备选方案在非 Linux 平台不可用，且需要内核特性支持（user namespace） | P2 | 仅作为 Linux 备选，不强制依赖 | 部署环境清单中标注内核版本要求 |

---

## 14. 跨端时间线 SQL 优化（TASK-0078 / R38-3, R32-2）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R78-1 | `fetch_by_time_range()` 新增方法依赖审计表有 `ts_ms` 列索引，无索引时大数据量查询仍慢 | P1 | 迁移脚本中为关键审计表的 `ts_ms` 列添加索引 | TASK-0078 执行时同步添加索引 |
| R78-2 | 分页查询 `LIMIT/OFFSET` 模式在深页码时性能退化（SQLite seek scan） | P2 | 默认 limit=100 + 实际使用场景页码不深 | 后续可改为 cursor-based 分页 |
| R78-3 | 面板 API 分页参数由前端传入，需校验防止恶意超大 limit/offset | P2 | 服务端硬限制 `limit ≤ 1000`，超出截断 | TASK-0078 实现时添加参数校验 |

---

## 15. Edge→Host RPC 事件上报（TASK-0079 / R38-1, R37-1）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R79-1 | 事件批量上报 HTTP 端点在高频上报时可能造成 Host FastAPI 线程阻塞 | P1 | 异步写入 + 批量入库策略 | TASK-0079 实现时使用异步端点 + 批量 INSERT |
| R79-2 | Edge 端事件缓存（网络中断时）可能占用大量内存（最多 1000 条/10MB 上限） | P2 | 硬限制缓存上限，超出丢弃最旧事件 + `EDGE_EVENT_CACHE_OVERFLOW` reason_code | 部署文档标注 Edge 端内存配置 |
| R79-3 | 事件补发顺序与原始顺序一致性——网络恢复后批量补发可能与新事件交叉 | P2 | 补发事件携带原始 `ts_ms`，Host 端按 `ts_ms` 排序而非接收顺序 | TASK-0079 确保排序逻辑正确 |

---

## 16. VTS WebSocket 自动重连（TASK-0080 / R34-2）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R80-1 | 指数退避重连期间表情映射请求可能堆积，重连成功后需清理过期队列 | P1 | 重连成功后清空待发送队列并 `reset_to_neutral` | TASK-0080 实现时处理队列清理 |
| R80-2 | VTS Plugin 端可能在重连后要求重新认证（Authentication Token 过期），重连逻辑需处理 re-auth | P1 | 重连流程中包含 re-auth 步骤，失败则视为重连失败 | TASK-0080 实现 re-auth 流程 |
| R80-3 | 最大重试 10 次（约 30 秒累计）后进入 DISCONNECTED，此期间所有 VTS 请求返回降级结果 | P2 | `VTS_RECONNECT_EXHAUSTED` reason_code + 人工介入提示 | 后续可增加后台持续低频探测恢复 |

---

## 17. ASR 麦克风设备自动恢复（TASK-0081 / R33-2）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R81-1 | 设备列表枚举在热插拔场景下可能有延迟（操作系统驱动刷新间隔） | P2 | 设备枚举间隔 ≥ 2 秒，避免频繁扫描 | 部署文档标注设备热插拔等待时间 |
| R81-2 | 备用设备自动切换可能选中非预期设备（如蓝牙耳机而非桌面麦克风） | P2 | 支持配置 `ASR_PREFERRED_DEVICES` 优先级列表 | TASK-0081 实现设备优先级配置 |
| R81-3 | 恢复流程中 ASR 暂停期间的音频数据不可追溯（静默丢弃） | P2 | `ASR_DEVICE_RECOVERING` 状态期间上报空 asr_chunk + 恢复时间戳 | 后续可增加缓冲区录音暂存 |

---

## 18. Host VTS DEGRADED 状态扩展（TASK-0082 / R37-2）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R82-1 | 新增 `DEGRADED` 枚举值需确保下游消费方（E2E 测试、面板 API）正确处理，避免未知状态异常 | P2 | 所有消费方使用 switch/match 且有 default 分支 | TASK-0082 执行时检查所有消费方 |
| R82-2 | 降级到 `neutral` 表情时，如果 `neutral` 本身在 VTS 端不存在，会产生二次降级（降级的降级） | P2 | `neutral` 作为硬编码 fallback，VTS 端必须预配置 | 部署文档标注 VTS neutral 表情必须存在 |

---

## 19. 部署文档与环境清单（TASK-0083）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R83-1 | 环境清单可能遗漏某些运行时隐式依赖（如系统级 libsndfile、portaudio） | P2 | 清单尽量覆盖，但实际部署仍需试运行验证 | 建立自动化环境检查脚本 |
| R83-2 | 文档维护可能落后于代码演进，配置项新增/废弃时需同步更新 | P2 | 文档与代码在同一 PR 中更新 | 建立 CI 检查：新增配置项必须有文档条目 |

---

## 风险等级统计（更新后）

| 等级 | 数量 | 说明 |
|------|------|------|
| **P0** | 0 | 无阻断性风险 |
| **P1** | 15 | 需跟进：沙箱安全、Edge 设备集成、跨端 RPC、SQL 性能、VTS 重连、Redis 验证、WAL 兼容 |
| **P2** | 28 | 已知边界/可接受：代码质量微调、平台差异、缓存优化、配置灵活性、文档维护 |

---

## P1 风险按优先级排序（更新后，建议跟进顺序）

| 优先级 | 风险编号 | 领域 | 摘要 | 关联 TASK |
|--------|---------|------|------|----------|
| 1 | R41-2 | 安全 | 沙箱非容器隔离，恶意代码可读主机文件 | TASK-0077 |
| 2 | R77-1 | 安全/跨平台 | Docker 隔离 Windows 不可用 | TASK-0077 |
| 3 | R77-2→R80-2 | VTS 重连 | 重连后需 re-auth | TASK-0080 |
| 4 | R41-1 | 跨平台 | subprocess 超时在 Windows/Linux 行为差异 | TASK-0077 |
| 5 | R80-1 | VTS 重连 | 重连期间表情队列堆积 | TASK-0080 |
| 6 | R33-1 | Edge GPU | ASR CPU 降级延迟可能不可接受 | — |
| 7 | R34-1 | Edge GPU | ASR+TTS 叠加 GPU OOM | — |
| 8 | R33-2 | Edge 音频 | 麦克风驱动异常无自动恢复 | TASK-0081 |
| 9 | R34-2 | Edge 网络 | VTS WebSocket 断连无自动重连 | TASK-0080 |
| 10 | R38-1 | 跨端集成 | Edge→Host RPC 上报通道未实现 | TASK-0079 |
| 11 | R79-1 | 跨端集成 | 高频事件上报阻塞 Host 线程 | TASK-0079 |
| 12 | R78-1 | 性能 | 时间线 SQL 查询无索引 | TASK-0078 |
| 13 | R37-1 | E2E 覆盖 | E2E 模拟 Edge 事件非真实 RPC | TASK-0079 |
| 14 | R38-3 | 性能 | 审计表内存遍历大数据量性能差 | TASK-0078 |
| 15 | R39-1 | 外部依赖 | Redis 后端未在真实环境验证 | — |

---

## 跨任务风险关联（更新后，系统性风险聚类）

### 聚类 A：Edge 设备资源竞争（R33-1, R34-1, R34-2）
- **描述**：ASR/TTS/VTS 三个 GPU 服务在 Edge 设备上共享有限 VRAM，叠加峰值可能导致级联 OOM。
- **缓解**：资源闸门优先级已定义（ASR>播放>TTS>兜底LLM），但未在真实设备上验证。
- **建议**：在目标 Edge 设备上进行压力测试，建立 VRAM 分配基线并动态调整。

### 聚类 B：Edge→Host 集成缺口（R37-1, R38-1, R79-1, R79-3）
- **描述**：当前 Host 层 E2E 测试和跨端时间线均通过模拟 Edge 事件验证，未经过真实 RPC 通道。TASK-0079 将填补此缺口，但高频上报和补发顺序仍需验证。
- **缓解**：EdgeEventReport 和 VoiceRpcContract 数据结构已定义好，可直接对接。
- **建议**：实现 edge_client/rpc 事件批量上报后，补充端到端集成测试并验证补发顺序正确性。

### 聚类 C：SQLite 性能天花板（R32-2, R36-2, R38-3, R78-1, R78-2）
- **描述**：审计、面板查询、时间线聚合均依赖 SQLite 单文件数据库，并发读写与数据增长可能成为瓶颈。TASK-0078 将增加时间窗口查询和索引，但深分页和大数据量仍有上限。
- **缓解**：WAL 模式 + busy_timeout + 重试策略已优化基本场景。
- **建议**：中期考虑引入 PostgreSQL 或只读 SQLite 副本，面板和时间线查询走只读连接。

### 聚类 D：沙箱安全级别（R41-1, R41-2, R77-1, R77-2, R77-3）
- **描述**：DRAFT_CODE 沙箱当前为 subprocess + 临时目录级隔离，非生产级容器隔离。TASK-0077 将升级为 Docker 容器级隔离，但 Windows 平台和离线环境存在限制。
- **缓解**：仅在 Host 开发阶段使用；执行结果不影响主链路状态。
- **建议**：生产部署前必须升级为 Docker/nsjail 容器级隔离，并添加 seccomp/AppArmor 配置。统一 Linux 部署以获得最佳隔离效果。

### 聚类 E：Edge 设备恢复韧性（R33-2, R34-2, R80-1, R80-2, R81-1, R81-2, R81-3）
- **描述**：ASR 麦克风和 VTS WebSocket 在设备异常/网络断连时的自动恢复能力是 Edge 端长期稳定运行的关键。TASK-0080/TASK-0081 将分别解决 VTS 重连和 ASR 设备恢复。
- **缓解**：错误码体系已定义（`ASR_DEVICE_ERROR`/`VTS_CONNECTION_FAILED`），健康快照可观测。
- **建议**：重连/恢复流程需处理认证、队列清理、设备优先级等细节；建议在真实设备上长时间稳定性测试。

### 聚类 F：代码质量与可维护性（R76-1~R76-6, R82-1, R82-2, R83-1, R83-2）
- **描述**：代码审查发现的 P2 级微调项（裸 print、类型标注、表名校验、列缓存、依赖说明）以及文档维护。
- **缓解**：均不影响运行时正确性，可集中批量修复。
- **建议**：TASK-0076 集中修复代码质量微调；TASK-0083 补充部署文档。建立 CI 流水线防止回归。

---

## 20. M9 World Canon 闭环（TASK-0111~0115 / R-85~R-89）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R111-1 | World Canon SSOT 文件（.json）直接写入磁盘，并发写入时可能产生文件损坏（无文件锁） | P1 | `upsert_document()` 通过 `_write_lock`（threading.Lock）串行化写入；单进程模型下无并发写入风险 | 多进程部署时需引入文件锁或数据库级写入 |
| R111-2 | `compute_world_canon_hash()` 使用 JSON 序列化后 SHA-256，字段顺序变化会导致 hash 不一致 | P2 | `json.dumps(sort_keys=True)` 保证字段顺序稳定 | 新增字段时需确保 sort_keys 覆盖 |
| R112-1 | `canon_sync` 健康探测（/health/canon_sync）失败时标记为 DEGRADED 非阻断，可能掩盖 SSOT 同步失败 | P1 | `STARTUP_CANON_SYNC_DEGRADED` 错误码可统计；start_host_side.ps1 输出 canon_sync_status 字段 | 监控告警需覆盖 STARTUP_CANON_SYNC_DEGRADED |
| R112-2 | `list_proposals()` 分页查询使用 `LIMIT/OFFSET`，深页码时性能退化 | P2 | 默认 limit=20，实际使用场景页码不深 | 后续可改为 cursor-based 分页 |
| R113-1 | context_pack 分槽注入（world_canon 槽）在 QA 拦截失败时降级为空槽，可能导致 LLM 输出缺少 Canon 约束 | P1 | `WCANON_SLOT_VIOLATION` 错误码可统计；降级时写 system_event_log | 监控告警需覆盖 WCANON_SLOT_VIOLATION |
| R114-1 | Evolution 提案审核流程（approve_proposal_with_content）直接修改 SSOT 文件，无回滚机制 | P1 | `canon_change_log` 记录变更前后 hash，可手动回滚；版本号单调递增 | 后续可实现 `rollback_proposal()` 方法 |
| R115-1 | 日记 drift_suggestion 关联（source_intent_id 串联）依赖 intent_id 在 diary_entry 和 canon_proposal_log 中一致，跨表关联可能因 intent_id 缺失而断链 | P2 | `source_intent_id` 为可选字段，缺失时降级为 None | 后续可增加 intent_id 完整性校验 |

---

## 21. M10-A Canon 治理 API（TASK-0117~0120 / R-90~R-91）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| R117-1 | `reject_proposal()` 对非 pending 提案抛出 PermissionError，API 层映射为 422；但 `approve_proposal_with_content()` 对非 pending 提案的错误类型需确认一致性 | P2 | 两者均使用 PermissionError + WCANON_PROPOSAL_STATUS_INVALID；T-Canon-API-05 已验证 | 确保所有状态转换错误统一使用 PermissionError |
| R118-1 | `_get_canon_store()` 使用全局单例（`_canon_store`），多线程并发初始化时可能产生竞态条件 | P1 | FastAPI 单进程模型下启动时序保证单例初始化；`world_root` 从环境变量读取 | 多进程部署时需使用进程级单例或依赖注入框架 |
| R118-2 | `canon_routes.py` 中 `_get_ledger_from_app()` 使用延迟导入（`from host_brain.app.fastapi_app import get_ledger`），循环导入风险存在 | P1 | 延迟导入在函数体内执行，避免模块级循环引用；与 `web_ui.py` 模式一致 | 后续重构时可改为依赖注入参数传递 |
| R118-3 | POST /canon/proposals 的幂等检查（重复 proposal_id 返回 409）依赖 `ledger.get_proposal()` 查询，高并发时两个请求可能同时通过检查并双写 | P1 | 单进程 + SQLite 串行写入保证原子性；`canon_proposal_log` 无 UNIQUE 约束 | 后续可在 `canon_proposal_log.proposal_id` 上添加 UNIQUE 约束 |
| R119-1 | approve 端点调用 `approve_proposal_with_content()` 后读取更新后的提案记录（`ledger.get_proposal()`），两次操作之间可能有状态不一致 | P2 | 单进程 + SQLite 串行写入，两次操作之间无并发修改风险 | 多进程部署时需使用事务包裹 |
| R119-2 | reject 端点写 `system_event_log`（`ledger.log_system_event()`）失败时只记录 warning，不阻断主流程——审计串联可能不完整 | P2 | 主流程（reject_proposal）已成功；审计写入失败不影响业务正确性 | 后续可将审计写入纳入事务，确保原子性 |
| R120-1 | `m10a_release.json` 中 `canon_routes_hash` 锁定了 API 实现文件，但未锁定 `canon_world_store.py`（含 reject_proposal）和 `sqlite_ledger.py`（含 list_proposals）的 hash | P2 | migration_hashes 已锁定数据库 schema；canon_routes_hash 锁定 API 层 | 后续可扩展 manifest 锁定更多核心文件 hash |

---

## 风险等级统计（更新至 M10-A）

| 等级 | 数量 | 说明 |
|------|------|------|
| **P0** | 0 | 无阻断性风险 |
| **P1** | 21 | 需跟进：M9/M10-A 新增 6 项（SSOT 并发写入、canon_sync 降级掩盖、分槽注入降级、Evolution 无回滚、canon_store 单例竞态、幂等双写）+ 原有 15 项 |
| **P2** | 35 | 已知边界/可接受：M9/M10-A 新增 7 项 + 原有 28 项 |

---

## M9/M10-A P1 风险按优先级排序（新增，建议跟进顺序）

| 优先级 | 风险编号 | 领域 | 摘要 | 关联 TASK |
|--------|---------|------|------|----------|
| 1 | R118-3 | 并发安全 | proposal_id 幂等检查无 UNIQUE 约束，高并发双写风险 | 后续 migration 添加 UNIQUE 约束 |
| 2 | R118-1 | 并发安全 | canon_store 全局单例多线程竞态 | 多进程部署时需依赖注入 |
| 3 | R118-2 | 架构 | canon_routes 延迟导入循环引用风险 | 后续重构为依赖注入参数传递 |
| 4 | R114-1 | 数据安全 | Evolution approve 无回滚机制 | 实现 rollback_proposal() |
| 5 | R112-1 | 可观测性 | canon_sync DEGRADED 非阻断可能掩盖同步失败 | 监控告警覆盖 STARTUP_CANON_SYNC_DEGRADED |
| 6 | R113-1 | 可观测性 | context_pack 分槽注入失败降级为空槽 | 监控告警覆盖 WCANON_SLOT_VIOLATION |
| 7 | R111-1 | 数据安全 | SSOT 文件并发写入无文件锁 | 多进程部署时引入文件锁 |

---

## 22. M17 前端 SPA（监控面板）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| BUG-FE-001 | `Overview.jsx` 汇总统计行读取不存在的 `data.summary` 字段，API 实际返回顶层 `total/up/degraded/down`，导致 4 个统计卡片始终显示 `—` | P0 | 无缓解，功能性 Bug | 修正字段读取路径：`data?.total` 等顶层字段 |
| WARN-FE-006 | `api/obs.js` 中 `fetchTraceNodes` 调用路径为 `/obs/trace/{id}/nodes`，后端路由为 `/obs/trace/{id}`（无 `/nodes` 后缀），当前 Mock 分支掩盖此问题 | P1 | Mock 数据降级掩盖，切换真实 API 后立即 404 | 修正前端 API 调用路径，或后端补充 `/nodes` 子路由 |
| WARN-FE-004 | `ServiceCard` 组件接收 `name/status/latency/detail` 属性，API 返回 `service_id/display_name/status/p95_ms/call_count`，字段名不匹配 | P1 | 服务卡片内容无法正确渲染 | 对齐 ServiceCard props 与 API 响应字段名 |
| WARN-FE-005 | `Sessions.jsx` 翻页时 `load` 的 `useCallback` 依赖 `page` 闭包陈旧值，翻页请求页码可能不正确 | P1 | 翻页功能异常 | 将 `page` 作为参数传入 `load` 而非依赖闭包 |
| WARN-FE-001 | `zustand` 实际版本 `5.0.2`，DevSpec §29.2 约定 `4.x`，版本偏差 | P2 | 当前代码使用基础 `create` 无 API 破坏 | 更新 DevSpec 版本约定为 5.x |
| WARN-FE-002 | Sessions → Trace 跳转实际使用路径参数 `/trace/:requestId`，DevSpec 约定为 hash query `#trace?rid=xxx` | P2 | 功能等价，仅文档不一致 | 更新 DevSpec §29.3.2 以反映实际实现 |
| WARN-FE-003 | `useAppStore.js` 缺少 `lastPolledAt` 和 `pollingError` 两个契约字段，轮询错误通过组件本地 state 管理 | P2 | 多页面无法共享轮询状态 | 补充 Store 字段，将轮询错误提升到全局 |
| WARN-FE-007 | Metrics 页 Mock 降级使用 `Math.random()` 生成数据，无"数据来源：模拟"标注，可能误导运维 | P2 | 防御性设计合理，仅缺少标注 | Mock 降级时显示明显的模拟数据标注 |

---

## 23. M18 后端 API 扩展（obs 模块）

| # | 风险描述 | 等级 | 缓解措施 | 后续行动 |
|---|---------|------|---------|---------|
| BUG-BE-001 | `obs_routes.py` Trace 降级路径访问 `ledger.governor_log`（不存在的属性），应使用 `ledger.fetch_by_time_range("governor_log", ...)`，当前降级路径触发时抛出 `AttributeError` → 500 | P0 | 无缓解，降级路径 500 | 改为 `ledger.fetch_by_time_range("governor_log", 0, _now_ms())` |
| BUG-BE-002 | `trace_node_builder.py` 四个 `_fetch_*_nodes` 方法均拉取全量审计日志（`ts_start=0`）后内存过滤 `request_id`，无时间范围约束 | P1 | 当前数据量小未暴露 | 增加 `request_id` 直接 SQL 过滤，或限制时间窗口 |
| WARN-BE-001 | `obs_routes.py` 的 `/obs/services/health` 端点与 `ServiceHealthAggregator` 类完全独立实现，服务列表不一致（Route 层 15 个 vs Service 层 15 个但分组不同） | P1 | Route 层功能更弱，维护成本高 | Route 层改为调用 `ServiceHealthAggregator` |
| WARN-BE-002 | `obs_routes.py` 的 `/obs/sessions` 端点与 `SessionQueryService` 类完全独立实现，Service 层支持更多过滤参数 | P1 | 功能不一致，维护成本高 | Route 层改为调用 `SessionQueryService` |
| WARN-BE-003 | `obs_routes.py` 的 `/obs/metrics/timeseries` 端点与 `MetricsTimeseriesService` 类完全独立实现，Service 层支持降采样和 reason_code 分布 | P1 | 功能不一致，维护成本高 | Route 层改为调用 `MetricsTimeseriesService` |
| WARN-BE-005 | `trace_node_builder.py` 的 `_infer_parent_ids` 使用简单线性链推断 parent，不支持并行分支结构，DevSpec §29.6.1 要求支持分支/并行 | P1 | Trace 图并行节点布局错误 | 改为基于 `node_type` 和时间戳的树形推断 |
| WARN-BE-004 | `ServiceHealthResult.to_dict()` 返回字段名 `name`，DevSpec §29.8.1 约定字段名为 `service_name` | P2 | 前端解析可能失败 | 修正 `to_dict()` 返回字段名 |
| WARN-BE-006 | `session_query.py` 的 `_fmt_ts` 使用本地时区 `datetime.fromtimestamp`，跨时区部署时显示错误时间 | P2 | 单机部署无影响 | 改为 UTC 时间或 ISO 8601 格式 |
| WARN-BE-007 | `metrics_timeseries.py` 的 `_compute_latency_dist` 仅有 `Overall` 维度，无法按 ASR/TTS/LLM 分解延迟，不满足 DevSpec §29.7.1 多服务维度要求 | P2 | 功能不完整 | 从各服务审计表分别聚合 P50/P95 |
| WARN-TEST-001 | `test_trace_node_builder.py` T-OBS-TRACE-03 未验证并行分支场景，测试通过不代表并行分支逻辑正确 | P2 | 测试覆盖不足 | 补充并行分支测试用例 |
| WARN-TEST-002 | `obs_routes.py` 4 个端点无集成测试，Route 层与 Service 层重复实现差异未被覆盖 | P2 | 测试覆盖缺口 | 补充 Route 层集成测试 |

---

## 风险等级统计（更新至 M17/M18）

| 等级 | 数量 | 说明 |
|------|------|------|
| **P0** | 2 | M17/M18 新增：BUG-FE-001（Overview 统计卡片）、BUG-BE-001（Trace 降级路径 500） |
| **P1** | 27 | M17/M18 新增 6 项（Trace API 路径不匹配、ServiceCard 字段名、Sessions 翻页、全量数据加载、Route/Service 三组平行实现、并行分支不支持）+ 原有 21 项 |
| **P2** | 43 | M17/M18 新增 8 项 + 原有 35 项 |

---

## M17/M18 P0/P1 风险按优先级排序（新增，建议跟进顺序）

| 优先级 | 风险编号 | 领域 | 摘要 | 建议行动 |
|--------|---------|------|------|----------|
| 1 | BUG-BE-001 | 后端 Bug | Trace 降级路径 AttributeError → 500 | 修正为 `fetch_by_time_range` 调用 |
| 2 | BUG-FE-001 | 前端 Bug | Overview 统计卡片始终显示 `—` | 修正字段读取路径 |
| 3 | WARN-FE-006 | 前后端对齐 | Trace API 路径不匹配，切换真实 API 后 404 | 对齐前端调用路径或补充后端子路由 |
| 4 | WARN-BE-001~003 | 架构 | Route 层与 Service 层三组平行实现 | Route 层改为调用 Service 层 |
| 5 | BUG-BE-002 | 性能 | TraceNodeBuilder 全量数据加载无时间约束 | 增加 SQL 级 request_id 过滤 |
| 6 | WARN-FE-004 | 前后端对齐 | ServiceCard 字段名不匹配 | 对齐 props 与 API 响应字段名 |
| 7 | WARN-FE-005 | 前端 Bug | Sessions 翻页闭包陈旧值 | 将 page 作为参数传入 |
| 8 | WARN-BE-005 | 功能缺失 | Trace 并行分支不支持 | 改为树形 parent 推断 |
