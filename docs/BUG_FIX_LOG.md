# Bug 修复日志 (BUG_FIX_LOG)

> 本文档记录 Natsume_Ai 项目代码全量排查过程中发现并修复的所有 Bug。
> 每轮排查结束后自动追加，优先级说明：🔴 P0（崩溃/功能完全失效）、🟠 P1（功能异常/数据错误）、🟡 P2（并发竞态/性能/兼容性）。

---

## 排查进度

| 项目 | 数量 |
|------|------|
| 总文件数 | 267 个 `.py` 文件 |
| 已排查 | 约 100 个（截至第 16 轮） |
| 剩余待排查 | 约 167 个 |
| 累计修复 Bug | **28 个**（P0×6、P1×14、P2×8） |

---

## 修复汇总总表

| 轮次 | 优先级 | 文件 | 修复内容 |
|------|--------|------|---------|
| 第1轮 | 🔴 P0 | `memory/episodic.py` | `_logger` 未定义，`NameError` |
| 第1轮 | 🟠 P1 | `governor/decision.py` | `_approve_fast` 跳过情感门控 |
| 第1轮 | 🟠 P1 | `memory/context_compressor.py` | `_hybrid` token 截断逻辑错误 |
| 第1轮 | 🟡 P2 | `core/metabolism.py` | 单条记录趋势永远 stable |
| 第1轮 | 🟡 P2 | `governor/rate_limit_store.py` | `degraded_from` 硬编码 BACKEND_REDIS |
| 第1轮 | 🟡 P2 | `channels/wechat.py` | `_normalize_ingress` 未捕获异常 |
| 第2轮 | 🔴 P0 | `bond/bond_stage_engine.py` | 积分恢复逻辑完全失效 |
| 第2轮 | 🟠 P1 | `mcp/tool_broker.py` | async handler 返回 coroutine 对象 |
| 第3轮 | 🔴 P0 | `airlock/sandbox.py` | `stderr=` 缩进错误，沙箱无法启动 |
| 第4轮 | 🔴 P0 | `scheduler/scheduler.py` | 心跳检测从未执行 |
| 第4轮 | 🟠 P1 | `retrieval/reranker.py` | 情感加权字段路径错误 |
| 第4轮 | 🟡 P2 | `memory/long_term_distiller.py` | `get_pending_proposals` 调用不存在方法 |
| 第5轮 | 🟠 P1 | `integrations/graph_branch.py` | `barrier_join` partial 模式语义错误 |
| 第5轮 | 🟡 P2 | `governor/tokens.py` | `TokenService` 并发修改 dict |
| 第6轮 | 🟠 P1 | `app/graph/main_chain.py` | 熔断器失败通知 `await` 同步方法 |
| 第7轮 | 🟠 P1 | `app/memory/memory_guard.py` | `get_event_loop()` 已废弃，Python 3.12+ 审计日志丢失 |
| 第8轮 | 🔴 P0 | `skills/impl/notification_executor.py` | `get_event_loop()` 已废弃 + 事件循环线程死锁风险 |
| 第9轮 | 🟠 P1 | `audit/retention.py` | `_delete_older_than` 绕过 `_write_lock`，并发 `database is locked` |
| 第10轮 | 🟠 P1 | `app/http_hooks.py` | `asyncio` 未导入，VTS 注册 `NameError` |
| 第11轮 | 🟠 P1 | `app/chat_handler_complex.py` | `_skill_manifest_for_graph` 重复构建 + warning 日志乱码 |
| 第12轮 | 🟠 P1 | `integrations/graph_runtime.py` | P8 图运行延迟监控 `latency_ms` 永远为 `None` |
| 第13轮 | 🟠 P1 | `integrations/graph_evidence.py` | `_connect()` 每次创建新连接不关闭，文件描述符泄漏 |
| 第14轮 | 🟡 P2 | `governor/policy_cache.py` | `GovernorPolicyCache` 无锁保护，多线程并发竞态 |
| 第15轮 | 🟡 P2 | `channels/edge_rpc_client.py` | `_EDGE_CLOCK_OFFSET_CACHE` 全局 dict 无锁保护，多线程并发竞态 |
| 第16轮 | 🔴 P0 | `core/qa_checks.py` | `CanonAsFactResult.violations` 引用未导入的 `QAViolation`，`NameError` |
| 第16轮 | 🟠 P1 | `retrieval/pipeline.py` | `recall()` 调用未传 `emotion_filter`，情感维度路由 M27-G 完全失效 |
| 第16轮 | 🟡 P2 | `retrieval/hybrid_fusion.py` | `_apply_hybrid_fusion_impl` 引用未定义的 `DEFAULT_RECALL_TOP_K` 和 `RecallFusionServiceContract` |

---

## 详细记录

---

### 第 1 轮

#### 🔴 P0 — `memory/episodic.py`：`_logger` 未定义

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/memory/episodic.py` |
| **根因** | 模块顶部缺少 `_logger = logging.getLogger(...)` 定义，但函数体内多处调用 `_logger.xxx()`，运行时触发 `NameError: name '_logger' is not defined` |
| **影响** | 任何触发情节记忆写入/读取的操作均会崩溃 |
| **修复** | 在文件顶部添加 `_logger = logging.getLogger("Natsume.memory.episodic")` |

---

#### 🟠 P1 — `governor/decision.py`：`_approve_fast` 跳过情感门控

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/governor/decision.py` |
| **根因** | `_approve_fast()` 快速审批路径直接返回 `GOV_OK`，完全跳过了 `rules.py` 中的 `_r8_emotion_gate()` 情感门控检查。当用户处于 `ANGRY`/`FRUSTRATED` 情绪且 Skill 风险等级为 `critical` 时，应触发情感门控降级，但快速路径绕过了这一逻辑 |
| **影响** | 高风险 Skill 在用户情绪激动时仍会被快速审批通过，违反情感安全策略 |
| **修复** | 在 `_approve_fast()` 中添加情感门控前置检查，检测到 `critical + ANGRY/FRUSTRATED` 时降级到完整审批路径 |

---

#### 🟠 P1 — `memory/context_compressor.py`：`_hybrid` token 截断逻辑错误

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/memory/context_compressor.py` |
| **根因** | `_hybrid()` 方法中 token 截断逻辑使用了错误的边界计算：`tokens[:max_tokens]` 截断后再 `join`，但 `max_tokens` 是字符数而非 token 数，导致截断位置不准确；且截断后没有重新计算实际 token 数，可能超出模型上下文窗口 |
| **影响** | 混合压缩模式下上下文可能超出模型 token 限制，导致 LLM 调用失败 |
| **修复** | 修正截断逻辑，使用正确的 token 计数方式，截断后重新验证 token 数 |

---

#### 🟡 P2 — `core/metabolism.py`：单条记录趋势永远 stable

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/core/metabolism.py` |
| **根因** | `_calc_trend()` 中当记录数为 1 时，`mid = len(records) // 2 = 0`，导致 `first_half = records[:0] = []`，`avg_first = sum([]) / len([])` 触发除零，但实际上代码有 `if mid > 0` 保护，`mid=0` 时直接返回 `"stable"`，导致单条记录永远返回 `"stable"` 而非 `None`（无法判断趋势） |
| **影响** | 代谢趋势分析在数据不足时给出误导性的 `"stable"` 结论 |
| **修复** | 当记录数 < 2 时返回 `None` 表示"数据不足，无法判断趋势" |

---

#### 🟡 P2 — `governor/rate_limit_store.py`：`degraded_from` 硬编码 BACKEND_REDIS

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/governor/rate_limit_store.py` |
| **根因** | `_degrade_to_memory()` 中 `degraded_from` 字段硬编码为 `"BACKEND_REDIS"`，即使实际后端是其他类型（如 Valkey、自定义 Redis 兼容实现），降级日志也会错误地记录为 Redis |
| **影响** | 降级监控日志不准确，影响故障排查 |
| **修复** | 改为从 `self._backend_type` 动态读取实际后端类型 |

---

#### 🟡 P2 — `channels/wechat.py`：`_normalize_ingress` 未捕获异常

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/channels/wechat.py` |
| **根因** | `_normalize_ingress()` 中对微信消息的 XML 解析没有 `try/except`，若收到格式异常的消息（非标准 XML、编码错误等），会向上抛出未处理异常，导致整个请求处理失败 |
| **影响** | 恶意或格式异常的微信消息可能导致服务崩溃 |
| **修复** | 添加 `try/except Exception` 捕获解析异常，返回标准化的错误响应 |

---

### 第 2 轮

#### 🔴 P0 — `bond/bond_stage_engine.py`：积分恢复逻辑完全失效

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/bond/bond_stage_engine.py` |
| **根因** | `_restore_points()` 方法中恢复积分的条件判断逻辑写反：`if current_points >= target_points` 时才执行恢复，但应该是 `current_points < target_points` 时才需要恢复。导致积分已经足够时才触发恢复，积分不足时反而不恢复 |
| **影响** | 羁绊积分恢复功能完全失效，用户积分无法正常恢复 |
| **修复** | 将条件判断改为 `if current_points < target_points` |

---

#### 🟠 P1 — `mcp/tool_broker.py`：async handler 返回 coroutine 对象

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/mcp/tool_broker.py` |
| **根因** | `ToolBroker.execute()` 是同步方法，直接调用 `handler(args, ctx)`。若 handler 是 `async def` 函数，调用结果是 coroutine 对象而非实际返回值，且 coroutine 不会被执行，导致工具调用结果永远是 coroutine 对象 |
| **影响** | 所有注册为 async 的 MCP 工具调用结果错误，工具功能完全失效 |
| **修复** | 在 `execute()` 中检测 handler 是否为协程函数，若是则通过 `asyncio.run()` 或 `run_coroutine_threadsafe()` 正确执行 |

---

### 第 3 轮

#### 🔴 P0 — `airlock/sandbox.py`：`stderr=` 缩进错误，沙箱无法启动

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/airlock/sandbox.py` |
| **根因** | `subprocess.Popen()` 调用中 `stderr=subprocess.PIPE` 参数的缩进错误，导致该参数被解析为独立语句而非函数参数，触发 `SyntaxError` 或 `TypeError` |
| **影响** | 沙箱进程无法启动，所有需要沙箱隔离的 Skill 执行失败 |
| **修复** | 修正 `stderr=subprocess.PIPE` 的缩进，使其正确作为 `Popen()` 的参数 |

---

### 第 4 轮

#### 🔴 P0 — `scheduler/scheduler.py`：心跳检测从未执行

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/scheduler/scheduler.py` |
| **根因** | `_heartbeat_loop()` 协程被创建但从未被 `await` 或 `asyncio.create_task()` 调度，导致心跳检测任务永远不会执行 |
| **影响** | 调度器心跳检测完全失效，无法检测到调度器异常状态，服务健康监控数据缺失 |
| **修复** | 在 `start()` 方法中使用 `asyncio.create_task(_heartbeat_loop())` 正确调度心跳任务 |

---

#### 🟠 P1 — `retrieval/reranker.py`：情感加权字段路径错误

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/retrieval/reranker.py` |
| **根因** | `_apply_emotion_boost()` 中读取候选项情感标签的字段路径为 `candidate.get("emotion_tag")`，但实际数据结构中情感标签存储在 `candidate.get("metadata", {}).get("emotion_tag")`，导致情感加权永远读取到 `None`，情感匹配加权完全失效 |
| **影响** | 情感维度的重排序（M27-G OPT-31）完全失效，情感相关的候选项无法被优先排序 |
| **修复** | 将字段路径修正为 `candidate.get("metadata", {}).get("emotion_tag")` |

---

#### 🟡 P2 — `memory/long_term_distiller.py`：`get_pending_proposals` 调用不存在方法

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/memory/long_term_distiller.py` |
| **根因** | `_fetch_pending_proposals()` 中调用 `self.ledger.get_pending_proposals()`，但 `SQLiteLedger` 中该方法名为 `fetch_pending_distillation_proposals()`，方法名不匹配导致 `AttributeError` |
| **影响** | 长期记忆蒸馏任务无法获取待处理提案，蒸馏功能失效 |
| **修复** | 将调用改为 `self.ledger.fetch_pending_distillation_proposals()` |

---

### 第 5 轮

#### 🟠 P1 — `integrations/graph_branch.py`：`barrier_join` partial 模式语义错误

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/integrations/graph_branch.py` |
| **根因** | `barrier_join()` 的 `partial` 模式下，判断条件为 `if arrived_count >= required_count`，但 `required_count` 被初始化为 `total_branches`（全部分支数），导致 `partial` 模式实际上等同于 `all` 模式，无法实现"部分分支到达即继续"的语义 |
| **影响** | 多分支并行图中的 partial barrier 语义完全错误，所有分支必须全部到达才能继续，违反设计意图 |
| **修复** | 将 `required_count` 初始化改为从 `barrier_config.min_required` 读取，默认为 `ceil(total_branches / 2)` |

---

#### 🟡 P2 — `governor/tokens.py`：`TokenService` 并发修改 dict

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/governor/tokens.py` |
| **根因** | `TokenService._buckets` 是普通 dict，`consume()`、`refill()`、`reset()` 方法均直接读写，无任何锁保护。在 FastAPI 多线程场景下并发调用时，可能触发 `RuntimeError: dictionary changed size during iteration` |
| **影响** | 高并发场景下 Token 服务可能崩溃，影响所有需要 Token 限流的功能 |
| **修复** | 引入 `threading.Lock()`，所有 `_buckets` 读写操作均通过 `with self._lock:` 保护 |

---

### 第 6 轮

#### 🟠 P1 — `app/graph/main_chain.py`：熔断器失败通知 `await` 同步方法

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/app/graph/main_chain.py` |
| **根因** | `_circuit_breaker_on_failure()` 是 `async def`，内部 `await self._notify_failure(...)` 调用了 `_notify_failure()`，但 `_notify_failure()` 是普通同步方法（`def`），`await` 一个同步方法会返回方法对象本身而非执行结果，通知逻辑完全不执行 |
| **影响** | 熔断器触发时失败通知永远不会发送，运维无法感知熔断事件 |
| **修复** | 将 `_notify_failure()` 改为 `async def`，或在调用处去掉 `await` |

---

### 第 7 轮

#### 🟠 P1 — `app/memory/memory_guard.py`：`get_event_loop()` 已废弃，Python 3.12+ 审计日志丢失

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/app/memory/memory_guard.py` |
| **根因** | `_write_audit_log()` 中使用 `asyncio.get_event_loop()` 获取事件循环，Python 3.12+ 在无运行中事件循环的同步上下文中调用此方法会抛出 `DeprecationWarning` 并最终在未来版本中报错；且当前版本在某些同步上下文中已会抛出 `RuntimeError`，导致审计日志写入失败 |
| **影响** | Python 3.12+ 环境下同步上下文中的审计日志写入失败，安全审计数据丢失 |
| **修复** | 改用 `asyncio.get_running_loop()`，`RuntimeError` 时降级为同步写入 |

---

### 第 8 轮

#### 🔴 P0 — `skills/impl/notification_executor.py`：`get_event_loop()` 已废弃 + 事件循环线程死锁风险

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/skills/impl/notification_executor.py` |
| **根因 1** | `execute_notify_tts_speak()` 使用已废弃的 `asyncio.get_event_loop()`，Python 3.12+ 无运行中事件循环时直接抛出 `RuntimeError`，导致 TTS 功能完全失效 |
| **根因 2** | `loop.is_running()` 为 `True` 时使用 `run_coroutine_threadsafe(coro, loop)` + `future.result(timeout=30)` 阻塞当前线程。若当前线程**就是**事件循环线程（FastAPI async 路由中），会造成**死锁**（事件循环等待 future，future 等待事件循环调度协程） |
| **影响** | Python 3.12+ 下 TTS 通知完全失效；FastAPI async 路由中调用时死锁，请求永久挂起 |
| **修复** | 改用 `asyncio.get_running_loop()`；有运行中事件循环时，用 `ThreadPoolExecutor` 在独立线程中执行 `asyncio.run(_call())`，彻底避免死锁；`RuntimeError` 时直接 `asyncio.run()` |

---

### 第 9 轮

#### 🟠 P1 — `audit/retention.py`：`_delete_older_than` 绕过 `_write_lock`，并发写入时 `database is locked`

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/audit/retention.py` |
| **根因** | `run_retention_policy()` 直接取 `conn = ledger._conn`，`_delete_older_than()` 中用 `with conn:` 执行 DELETE，**完全绕过了 `ledger._write_lock`**。`SQLiteLedger` 的所有写入方法（`_insert`、`update_proposal_status` 等）都通过 `_write_lock` 保护并发安全，直接操作 `_conn` 会在并发写入时触发 `sqlite3.OperationalError: database is locked` |
| **影响** | 保留策略执行时与其他写入操作并发，触发 SQLite 锁错误，保留策略执行失败 |
| **修复** | 将 `write_lock = ledger._write_lock` 传入 `_delete_older_than()`，在 DELETE 操作外层加 `with write_lock:`，与 `SQLiteLedger._insert` 的加锁策略保持一致 |

---

### 第 10 轮

#### 🟠 P1 — `app/http_hooks.py`：`asyncio` 未导入，VTS 主事件循环注册 `NameError`

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/app/http_hooks.py` |
| **根因** | 文件顶部缺少 `import asyncio`，但 `runtime_lifespan()` 中调用了 `asyncio.get_running_loop()`，运行时抛出 `NameError: name 'asyncio' is not defined`，导致 VTS 主事件循环注册失败 |
| **影响** | 应用启动时 VTS 主事件循环注册失败，`run_coroutine_threadsafe` 无法正确提交 VTS 驱动协程到主循环，VTS 功能完全失效 |
| **修复** | 在文件顶部添加 `import asyncio` |

---

### 第 11 轮

#### 🟠 P1 — `app/chat_handler_complex.py`：`_skill_manifest_for_graph` 重复构建 + warning 日志乱码

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/app/chat_handler_complex.py` |
| **根因 1** | `_skill_manifest_for_graph` 在 `SCENE_EXEC_INTENT` 场景下被构建了两次：第一次在 retrieval 等待之前（结果传入 `build_chat_runtime_input`），第二次在 `graph_start_ms` 之后（结果传入 `graph_payload`）。两次构建时机不同，导致 `runtime_input` 和 `graph_payload` 中的 skill_manifest 来自不同时刻，存在不一致风险 |
| **根因 2** | 第一次构建的 warning 日志中含有 UTF-8 被 GBK 误读的乱码字符串 `鏋勫缓澶辫触锛堥檷绾т负绌哄垪琛級` |
| **影响** | skill_manifest 不一致可能导致图执行时使用错误的技能列表；乱码日志影响故障排查 |
| **修复** | 删除第一次多余的构建代码块（9 行），保留第二次构建；修复 warning 日志为正确中文 `skill_manifest 构建失败（降级为空列表）` |

---

### 第 12 轮

#### 🟠 P1 — `integrations/graph_runtime.py`：P8 图运行延迟监控 `latency_ms` 永远为 `None`

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/integrations/graph_runtime.py` |
| **根因** | `_write_p8_ledger()` 中 `log_p8_graph_run` 的 `latency_ms` 字段被硬编码为 `None`，注释说"由 run_graph 在 _finalize 后填充"，但实际上 `run_graph` 传入的 `run_start_ms=now_value` 和 `ts_ms=now_value` 是同一时刻（`run_graph` 入口），导致无法计算耗时 |
| **影响** | P8 图运行延迟监控数据全部为 `None`，监控仪表盘无法展示图执行耗时，性能分析失效 |
| **修复** | 在 `compiled.invoke()` 前后分别采样 `_graph_run_start_ms` 和 `_graph_run_end_ms`；在 `_write_p8_ledger()` 中改为 `actual_latency_ms = ts_ms - run_start_ms` 计算真实耗时 |

---

### 第 13 轮

#### 🟠 P1 — `integrations/graph_evidence.py`：`_connect()` 每次创建新连接不关闭，文件描述符泄漏

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/integrations/graph_evidence.py` |
| **根因** | 非 `:memory:` 模式下，`_connect()` 每次调用都返回 `sqlite3.connect(...)` 的新连接对象。调用方使用 `with self._connect() as conn:` 语法，但 `sqlite3.Connection.__exit__` 只做 commit/rollback，**不关闭连接**。每次图执行写入多条 trace，每条都创建一个新连接且永不关闭 |
| **影响** | 高频场景下（每次对话写多条 Evidence trace）文件描述符持续增长，最终耗尽系统文件描述符限制（默认 1024），导致进程崩溃 |
| **修复** | 引入 `threading.local()` 线程本地存储，每个线程缓存一个 SQLite 连接复用，避免重复创建；`:memory:` 模式保持原有行为不变 |

---

### 第 14 轮

#### 🟡 P2 — `governor/policy_cache.py`：`GovernorPolicyCache` 无锁保护，多线程并发竞态

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/governor/policy_cache.py` |
| **根因** | `get()`、`put()`、`invalidate()`、`clear()` 方法均直接操作 `self._cache` 字典，无任何锁保护。在 FastAPI 多线程场景（`asyncio.to_thread` 调用 Governor 审批）下，`del self._cache[key]`（TTL 过期删除）与 `self._cache[key] = ...`（写入）并发执行，可能触发 `RuntimeError: dictionary changed size during iteration` |
| **影响** | 高并发场景下 Governor 策略缓存可能崩溃，影响所有需要审批的 Skill 执行 |
| **修复** | 引入 `threading.Lock()`，所有 `_cache` 读写操作均通过 `with self._lock:` 保护，与 `TokenService`（第5轮修复）保持一致的并发安全策略 |

---

### 第 15 轮

#### 🟡 P2 — `channels/edge_rpc_client.py`：`_EDGE_CLOCK_OFFSET_CACHE` 全局 dict 无锁保护，多线程并发竞态

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/channels/edge_rpc_client.py` |
| **根因** | `_resolve_edge_clock_offset_ms()` 和 `_invalidate_edge_clock_offset_cache()` 均直接读写模块级全局 dict `_EDGE_CLOCK_OFFSET_CACHE`，无任何锁保护。在多线程并发调用 `post_vts`/`post_speak` 时，`_EDGE_CLOCK_OFFSET_CACHE.update(...)` 与 `_EDGE_CLOCK_OFFSET_CACHE.get(...)` 并发执行，可能读到中间状态 |
| **影响** | 时钟校准逻辑误判缓存命中，导致 VTS 时间戳偏差，表情/动作触发时序错误 |
| **修复** | 引入 `_EDGE_CLOCK_OFFSET_LOCK = threading.Lock()`，所有对 `_EDGE_CLOCK_OFFSET_CACHE` 的读写均通过 `with _EDGE_CLOCK_OFFSET_LOCK:` 保护；注意 `_resolve_edge_clock_offset_ms` 是 `async` 函数，锁只在同步读写 dict 的瞬间持有，不会阻塞 `await` 的 HTTP 请求 |

---

### 第 16 轮

#### 🔴 P0 — `core/qa_checks.py`：`CanonAsFactResult.violations` 引用未导入的 `QAViolation`，`NameError`

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/core/qa_checks.py` |
| **根因** | `qa_checks.py` 是从 `qa.py` 拆分出来的模块，但拆分时遗漏了必要的导入：`CanonAsFactResult.violations: list[QAViolation]` 引用了 `QAViolation`；`check_canon_as_fact()` 内部调用了 `LintResult`、`_validate_canon_as_fact`、`VIOLATION_CANON_AS_FACT`，这些符号均未在 `qa_checks.py` 中定义，也未从 `qa.py` 导入 |
| **影响** | 任何调用 `check_canon_as_fact()` 的代码都会触发 `NameError`，QA 检查功能完全失效 |
| **修复** | 在 `qa_checks.py` 顶部添加 `from host_brain.core.qa import LintResult, QAViolation, VIOLATION_CANON_AS_FACT, _validate_canon_as_fact` |

---

#### 🟠 P1 — `retrieval/pipeline.py`：`recall()` 调用未传 `emotion_filter`，情感维度路由 M27-G 完全失效

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/retrieval/pipeline.py` |
| **根因** | `RetrievalPipelineContract.run()` 接收 `emotion_context` 参数并透传给 `reranker_service.rerank(emotion_context=emotion_context)`，但调用 `recall_service.recall()` 时**完全没有传入** `emotion_filter` 参数（`recall()` 的参数名是 `emotion_filter`，不是 `emotion_context`） |
| **影响** | M27-G 情感维度路由（将情感匹配的候选项排在前面）在整个 pipeline 层永远不会生效，情感相关记忆无法被优先召回 |
| **修复** | 在 `recall_service.recall()` 调用中添加 `emotion_filter=emotion_context` |

---

#### 🟡 P2 — `retrieval/hybrid_fusion.py`：`_apply_hybrid_fusion_impl` 引用未定义的 `DEFAULT_RECALL_TOP_K` 和 `RecallFusionServiceContract`

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/retrieval/hybrid_fusion.py` |
| **根因** | `_apply_hybrid_fusion_impl` 是从 `recall_fusion.py` 迁移过来的遗留函数，引用了 `DEFAULT_RECALL_TOP_K`（未在 `hybrid_fusion.py` 中定义）和 `RecallFusionServiceContract`（仅在类型注释中使用，但未通过 `TYPE_CHECKING` 导入）。若该函数被调用，会触发 `NameError` |
| **影响** | 若 `_apply_hybrid_fusion_impl` 被调用，触发 `NameError`；`RecallFusionServiceContract` 类型注解在运行时无法解析 |
| **修复** | 在文件顶部添加 `DEFAULT_RECALL_TOP_K = 8`、`TYPE_CHECKING` 导入和 `RecallFusionServiceContract` 的类型导入 |

---

---

#### 🟡 P2 — `skills/registry.py`：`PermissionGuard` 调度器计数器无锁保护，多线程并发竞态

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/skills/registry.py` |
| **根因** | `PermissionGuard._check_scheduler_permissions` 中对 `_scheduler_call_counts` 和 `_scheduler_count_timestamps` 的读写（`pop`、`get`、`__setitem__`）没有任何锁保护。在 FastAPI 多线程场景下，并发的 SCHEDULER 调用可能导致计数器竞态，使得调用次数超过 `SCHEDULER_MAX_TOOL_CALLS=3` 的限制，允许超额调用高风险工具 |
| **影响** | SCHEDULER 来源的工具调用次数限制失效，可能允许超过 3 次/周期的调用，绕过安全限制 |
| **修复** | 在 `__init__` 中添加 `self._lock = threading.Lock()`，在 `_check_scheduler_permissions` 和 `reset_scheduler_count` 中用 `with self._lock:` 保护所有计数器读写操作 |

---

### 📊 累计修复汇总（全部轮次）

| 轮次 | 优先级 | 文件 | 修复内容 |
|------|--------|------|---------|
| 第1轮 | 🔴 P0 | `memory/episodic.py` | `_logger` 未定义 |
| 第1轮 | 🟠 P1 | `governor/decision.py` | `_approve_fast` 跳过情感门控 |
| 第1轮 | 🟠 P1 | `memory/context_compressor.py` | `_hybrid` token 截断逻辑错误 |
| 第1轮 | 🟡 P2 | `core/metabolism.py` | 单条记录趋势永远 stable |
| 第1轮 | 🟡 P2 | `governor/rate_limit_store.py` | `degraded_from` 硬编码 BACKEND_REDIS |
| 第1轮 | 🟡 P2 | `channels/wechat.py` | `_normalize_ingress` 未捕获异常 |
| 第2轮 | 🔴 P0 | `bond/bond_stage_engine.py` | 积分恢复逻辑完全失效 |
| 第2轮 | 🟠 P1 | `mcp/tool_broker.py` | async handler 返回 coroutine 对象 |
| 第3轮 | 🔴 P0 | `airlock/sandbox.py` | `stderr=` 缩进错误 |
| 第4轮 | 🔴 P0 | `scheduler/scheduler.py` | 心跳检测从未执行 |
| 第4轮 | 🟠 P1 | `retrieval/reranker.py` | 情感加权字段路径错误 |
| 第4轮 | 🟡 P2 | `memory/long_term_distiller.py` | `get_pending_proposals` 调用不存在方法 |
| 第5轮 | 🟠 P1 | `integrations/graph_branch.py` | `barrier_join` partial 模式语义错误 |
| 第5轮 | 🟡 P2 | `governor/tokens.py` | `TokenService` 并发修改 dict |
| 第6轮 | 🟠 P1 | `app/graph/main_chain.py` | 熔断器失败通知 `await` 同步方法 |
| 第7轮 | 🟠 P1 | `app/memory/memory_guard.py` | `get_event_loop()` 已废弃，Python 3.12+ 审计日志丢失 |
| 第8轮 | 🔴 P0 | `skills/impl/notification_executor.py` | `get_event_loop()` 已废弃 + 死锁风险 |
| 第9轮 | 🟠 P1 | `audit/retention.py` | `_delete_older_than` 绕过 `_write_lock`，并发 `database is locked` |
| 第10轮 | 🟠 P1 | `app/http_hooks.py` | `asyncio` 未导入，VTS 注册 `NameError` |
| 第11轮 | 🟠 P1 | `app/chat_handler_complex.py` | `_skill_manifest_for_graph` 重复构建 + 日志乱码 |
| 第12轮 | 🟠 P1 | `integrations/graph_runtime.py` | P8 图运行延迟监控 `latency_ms` 永远为 `None` |
| 第13轮 | 🟠 P1 | `integrations/graph_evidence.py` | `_connect()` 每次创建新连接不关闭，文件描述符泄漏 |
| 第14轮 | 🟡 P2 | `governor/policy_cache.py` | `GovernorPolicyCache` 无锁保护，多线程并发竞态 |
| 第15轮 | 🟡 P2 | `channels/edge_rpc_client.py` | `_EDGE_CLOCK_OFFSET_CACHE` 全局 dict 无锁保护，多线程并发竞态 |
| 第16轮 | 🔴 P0 | `core/qa_checks.py` | `check_canon_as_fact()` 引用未导入符号，`NameError` 完全失效 |
| 第16轮 | 🟠 P1 | `retrieval/pipeline.py` | `recall()` 未传 `emotion_filter`，情感维度路由 M27-G 完全失效 |
| 第16轮 | 🟡 P2 | `retrieval/hybrid_fusion.py` | `_apply_hybrid_fusion_impl` 引用未定义 `DEFAULT_RECALL_TOP_K` |
| **第17轮** | 🟡 P2 | `skills/registry.py` | `PermissionGuard` 调度器计数器无锁保护，多线程并发竞态 |
| **第18轮** | 🟠 P1 | `integrations/llm_gateway.py` | `update_route_config` 热更新无锁保护，并发读写竞态 |
| **第18轮** | 🟡 P2 | `scheduler/task_registry.py` | `SchedulerTaskRegistry._tasks` 无实例锁保护，并发注册/注销竞态 |
| **第19轮** | 🟠 P1 | `app/agents/tool_registry.py` | `_run_async` 在事件循环线程中调用 `run_coroutine_threadsafe().result()` 导致死锁 |
| **第19轮** | 🟠 P1 | `app/system_handler.py` | VTS 驱动使用 `get_event_loop()` 已废弃，Python 3.12+ 抛出 `RuntimeError` |
| **第20轮** | 🟡 P3 | `app/handlers/intent_agent_entry_handler.py` | `for...else` 死代码块（`else` 永远不执行）+ 循环体缩进错误 |
| **第21轮** | 🔴 P1 | `integrations/graph_executor.py` + `integrations/graph_runtime.py` | `CheckpointManager` 实例不共享：`run_graph` 中 `GraphExecutor` 创建独立实例，`resume_graph` 使用 `GraphRuntimeContract` 的实例，`interrupt()` 保存的 checkpoint 在 `load_state()` 中永远找不到 |
| **第22轮** | 🟠 P2 | `integrations/graph_subgraph.py` | `invoke()` 中 `final_status` 逻辑错误：子图 `DEGRADED` 状态覆盖主图已有的 `FAILED` 状态，应取两者中更严重的状态 |

**第22轮排查文件：8 个 | 累计已排查：约 164 个 | 剩余约 103 个**

---

#### 🟡 P3 — `app/handlers/intent_agent_entry_handler.py`：`for...else` 死代码块 + 缩进错误

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/app/handlers/intent_agent_entry_handler.py` `intent_agent_entry_handler` |
| **根因 1** | `for attempt in range(...): ... break ... except: ... if attempt >= max_retry_count: return ...` 后跟 `else: if last_exc is not None: return ...`。Python `for...else` 的 `else` 块只在循环未被 `break` 中断时执行。但失败路径已通过 `return` 退出，`else` 块**永远不会执行**，是死代码，会误导维护者认为有兜底逻辑 |
| **根因 2** | `for attempt` 循环体的 `try/except` 块缩进多了一级（12空格而非8空格），虽然 Python 允许，但与项目其他代码风格不一致，且与 `for` 语句的缩进关系不直观 |
| **影响** | 死代码不影响功能，但维护者可能误以为 `else` 块是有效的兜底逻辑，在修改重试逻辑时产生误判 |
| **修复** | 移除 `last_exc` 变量和 `for...else` 的 `else` 块，修正 `try/except` 缩进为标准4空格 |

---

#### 🔴 P1 — `integrations/graph_executor.py` + `integrations/graph_runtime.py`：`CheckpointManager` 实例不共享

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/integrations/graph_executor.py` `GraphExecutor.__init__` + `host_brain/integrations/graph_runtime.py` `GraphRuntimeContract.run_graph` / `resume_graph` |
| **根因** | `GraphExecutor.__init__` 中 `self._checkpoint_manager = CheckpointManager()` 创建了一个**独立的内存 CheckpointManager**。`run_graph` 中每次调用 `GraphExecutor(...)` 都创建新实例，`graph_executor.py` 中 `interrupt()` 将 checkpoint 保存到 `GraphExecutor._checkpoint_manager._store`（内存字典）。而 `resume_graph` 中使用 `self._checkpoint_manager`（`GraphRuntimeContract` 的实例），这是**另一个独立的 CheckpointManager**，其 `_store` 为空，`load_state()` 永远返回 `None`，导致 `resume_graph` 始终返回 `CHECKPOINT_NOT_FOUND` |
| **影响** | 断点续跑（M39-F）功能完全失效：所有 AIRLOCK 暂停后的恢复请求都会返回 `CHECKPOINT_NOT_FOUND`，无法从中断点继续执行 |
| **修复** | `GraphExecutor.__init__` 新增 `checkpoint_manager` 参数（可选），若传入则使用外部共享实例，否则创建新实例。`GraphRuntimeContract.run_graph` 和 `resume_graph` 中创建 `GraphExecutor` 时传入 `checkpoint_manager=self._checkpoint_manager`，确保两者共享同一个 `CheckpointManager` 实例 |

---

#### 🟠 P2 — `integrations/graph_subgraph.py`：`invoke()` 中 `final_status` 逻辑错误

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/integrations/graph_subgraph.py` `CompiledSubgraph.invoke()` |
| **根因** | `final_status` 计算逻辑错误：`final_status if final_status != STATUS_OK else str(state.get("status") or STATUS_OK)`。当子图有节点失败（`current_status = DEGRADED`）时，`final_status = DEGRADED`，直接写入 `state["status"]`，**覆盖了主图已有的 `FAILED` 状态**。注释写的是

#### 🟠 P1 — `app/agents/tool_registry.py`：`_run_async` 在事件循环线程中死锁

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/app/agents/tool_registry.py` |
| **根因** | `_run_async` 检测到已有运行中的事件循环时，使用 `asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=5.0)`。`result()` 是阻塞调用，会阻塞当前线程。若当前线程**就是**事件循环线程（FastAPI async 上下文中同步调用工具），则 `result()` 永久阻塞（死锁），因为事件循环被阻塞无法执行提交的协程 |
| **影响** | 在 FastAPI async 上下文中调用任何使用 `_run_async` 的工具（`search_knowledge`、`search_by_keyword`、`search_by_vector`）时，请求永久挂起，直到 5s 超时后抛出 `concurrent.futures.TimeoutError` |
| **修复** | 改为在独立线程中执行阻塞等待：创建 `threading.Thread`，在线程中调用 `run_coroutine_threadsafe().result()`，主线程通过 `t.join(timeout=6.0)` 等待结果，避免阻塞事件循环线程 |

---

#### 🟠 P1 — `app/system_handler.py`：VTS 驱动使用 `get_event_loop()` 已废弃

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/app/system_handler.py` `receive_edge_events_response` |
| **根因** | `loop = _asyncio.get_event_loop()` 在 Python 3.10+ 的非 async 上下文中已废弃，Python 3.12+ 会直接抛出 `DeprecationWarning` 并最终升级为 `RuntimeError`。`receive_edge_events_response` 是同步函数，在 FastAPI 的同步路由中调用，属于非 async 上下文 |
| **影响** | Python 3.12+ 环境下，Edge 事件上报中包含情感事件时，VTS 驱动代码抛出 `RuntimeError`，导致整个情感事件后处理块失败（Bond 更新和 VTS 驱动均跳过） |
| **修复** | 将 `_asyncio.get_event_loop()` 改为 `_asyncio.get_running_loop()`，在非 async 上下文中 `get_running_loop()` 会抛出 `RuntimeError`，被已有的 `except RuntimeError: pass` 捕获，正确跳过 VTS 驱动 |

---

*文档最后更新：第 19 轮排查完成*

#### 🟠 P1 — `integrations/llm_gateway.py`：`update_route_config` 热更新无锁保护，并发读写竞态

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/integrations/llm_gateway.py` |
| **根因** | `update_route_config` 直接修改 `self.provider_name`、`self.available`、`self.timeout_ms`、`self.fallback_providers`，而 `generate`、`route_call`、`_provider_available` 同时读取这些字段，没有任何锁保护。在 FastAPI 多线程并发场景下（热更新 API + 正常请求并发），可能读到中间状态（如 `provider_name` 已更新但 `api_key` 还是旧值），导致路由决策错误 |
| **影响** | LLM 路由热更新期间，并发请求可能使用不一致的配置（新 provider_name + 旧 api_key），导致 API Key 校验失败或路由到错误的模型 |
| **修复** | 引入 `self._route_lock = threading.Lock()`，在 `_provider_available` 中用锁保护配置字段读取，在 `update_route_config` 中用锁保护 before_state 读取和新配置写入（原子操作） |

---

#### 🟡 P2 — `scheduler/task_registry.py`：`SchedulerTaskRegistry._tasks` 无实例锁保护，并发注册/注销竞态

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/scheduler/task_registry.py` |
| **根因** | `SchedulerTaskRegistry` 的类级别 `_lock` 仅用于单例创建（双重检查锁），实例方法 `register`、`unregister`、`get`、`all_tasks`、`enabled_tasks`、`count` 直接读写 `self._tasks` dict，没有实例级别的锁保护。在调度器启动时并发注册任务，或运行时动态注销任务时，可能导致 dict 竞态 |
| **影响** | 并发注册任务时可能绕过 `task_id` 重复检查，导致任务被覆盖；并发读写时可能触发 `RuntimeError: dictionary changed size during iteration` |
| **修复** | 在 `__init__` 中添加 `self._tasks_lock = threading.Lock()`，所有 `_tasks` 读写操作均通过 `with self._tasks_lock:` 保护 |

---

*文档最后更新：第 18 轮排查完成*

---

#### 🟠 P2 — `integrations/graph_subgraph.py`：`invoke()` 中 `final_status` 逻辑错误

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/integrations/graph_subgraph.py` `CompiledSubgraph.invoke()` |
| **根因** | `final_status` 计算逻辑错误：`final_status if final_status != STATUS_OK else str(state.get("status") or STATUS_OK)`。当子图有节点失败（`current_status = DEGRADED`）时，`final_status = DEGRADED`，直接写入 `state["status"]`，**覆盖了主图已有的 `FAILED` 状态**。注释写的是 `# 子图状态降级时，主图状态同步（但不覆盖 FAILED）`，但实现是反的 |
| **影响** | 主图执行过程中若某节点已将 `state["status"]` 设为 `FAILED`，子图执行后若有节点失败（`DEGRADED`），会将主图状态从 `FAILED` 降级为 `DEGRADED`，导致后续节点误判主图状态，可能继续执行本应终止的流程 |
| **修复** | 引入 `_STATUS_SEVERITY` 优先级字典（`FAILED=2 > DEGRADED=1 > OK=0`），取 `current_status` 和 `original_status` 中严重度更高的那个作为 `final_status`，确保 `FAILED` 状态不被 `DEGRADED` 覆盖 |

---

*文档最后更新：第 22 轮排查完成*

---

#### 🔴 P1 — `app/memory/memory_guard.py`：`check_distillation_eligibility` 阈值硬编码为 1，与需求 3.3 不符

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/app/memory/memory_guard.py` `MemoryGuard.check_distillation_eligibility()` |
| **根因** | 方法内注释写 `# 降低阈值：频次 ≥ 1 即可蒸馏`，阈值硬编码为 `>= 1`，但需求 3.3 明确要求 `频次 ≥ 3 OR MILESTONE`，且 `DistillationEngine` 的 `min_occurrence` 默认值也是 3。导致 `EpisodicStore.write()` 中每条写入的记录都被标记为 `distill_eligible=True`，`EpisodicWriteResult.distill_eligible` 字段语义失真 |
| **影响** | 调用方若依赖 `distill_eligible=True` 决定是否立即触发单条蒸馏（`distill_single()`），会对所有写入记录都触发蒸馏，绕过频次门槛，导致低质量记忆被过早蒸馏为 Canon 提案 |
| **修复** | 为 `check_distillation_eligibility()` 添加 `min_occurrence: int = 3` 参数，恢复正确阈值；同步为 `EpisodicStore.__init__()` 添加 `min_occurrence: int = 3` 参数，并在 `write()` 中传入 `self._min_occurrence`，确保两者保持一致 |

---

**第23轮排查文件：8 个 | 累计已排查：约 172 个 | 剩余约 95 个**

*文档最后更新：第 23 轮排查完成*

---

#### 🔴 P1 — `app/vision/edge_rpc_client.py`：`capture_screenshot` 返回字段名 `image_b64` 与 `agent_loop.py` 消费字段名 `image_base64` 不一致

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/app/vision/edge_rpc_client.py` `EdgeRpcClient.capture_screenshot()` |
| **根因** | `EdgeRpcClient.capture_screenshot()` 返回字典中使用 `image_b64` 字段名，但 `agent_loop.py` 中消费时使用 `screenshot_data.get("image_base64", "")`，导致使用 `EdgeRpcClient` 时截图数据永远为空字符串 |
| **影响** | Agent Loop 每轮截图后 `vision_context["image_base64"]` 始终为 `""`，LLM 无法获取截图内容，每轮都生成降级计划（`PLAN_FAILED`），Agent Loop 无法正常工作 |
| **修复** | 将 `EdgeRpcClient.capture_screenshot()` 返回字段统一改为 `image_base64`，同时兼容 Edge 端可能返回 `image_b64` 或 `image_base64` 两种字段名 |

---

#### 🟠 P2 — `app/vision/agent_loop.py`：`goal_achieved` 判断错误依赖 `report.status == ActionStatus.SUCCESS`

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/app/vision/agent_loop.py` `run_agent_loop()` 步骤4 |
| **根因** | `if report.goal_achieved or report.status == ActionStatus.SUCCESS` 中，`status == SUCCESS` 仅表示所有指令执行成功，不代表语义目标达成。当 ClawdBot 执行完所有指令但目标未达成时（`goal_achieved=False, status=SUCCESS`），此处会错误地将 `goal_achieved` 置为 `True` |
| **影响** | Agent Loop 在目标未达成时提前终止，不再继续下一轮截图分析，导致自动化任务在"指令执行成功但目标未达成"的场景下无法继续重试 |
| **修复** | 移除 `or report.status == ActionStatus.SUCCESS` 条件，仅依赖 `report.goal_achieved` 判断目标是否达成，并添加注释说明两者语义区别 |

---

**第24轮排查文件：8 个 | 累计已排查：约 180 个 | 剩余约 87 个**

*文档最后更新：第 24 轮排查完成*

---

#### 🔴 P1 — `app/vision/edge_rpc_client.py`：`_convert_plan_to_desktop_actions` 读取 `action.x2/action.y2`，但 `Action` 模型字段名为 `to_x/to_y`（上轮遗留技术债，本轮修复）

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/app/vision/edge_rpc_client.py` `_convert_plan_to_desktop_actions()` |
| **根因** | `Action` 是 Pydantic `BaseModel`，拖拽终点字段名为 `to_x/to_y`，但 `_convert_plan_to_desktop_actions` 中读取 `action.x2/action.y2`，访问不存在的字段会抛 `AttributeError`，被外层 `try/except` 吞掉，返回 `FAILED` |
| **影响** | 所有包含 `DRAG` 操作的 `ActionPlan` 执行时，拖拽终点坐标丢失，Edge 端收到的 `x2/y2` 为空，拖拽操作全部失败 |
| **修复** | 将 `action.x2/action.y2` 改为 `action.to_x/action.to_y`，并添加注释说明字段名映射关系 |

---

#### 🔴 P1 — `bond/bond_stage_engine.py`：`_restore_state_from_log` 只查 `bond_update_log`，漏查 `bond_stage_transition_log`，导致进程重启后 Bond 积分恢复偏差

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/bond/bond_stage_engine.py` `BondStageEngine._restore_state_from_log()` |
| **根因** | `_log_transition` 中优先调用 `ledger.log_bond_stage_transition()`，将 `STAGE_TRANSITION` 记录写入 `bond_stage_transition_log` 表；但 `_restore_state_from_log` 只查 `bond_update_log` 表，导致找不到 `STAGE_TRANSITION` 记录，降级为累加模式 |
| **影响** | 进程重启后，Bond 积分恢复时无法找到最近的 `STAGE_TRANSITION` 快照，只能从头累加历史事件积分，可能因历史负分事件（`IDLE_DECAY`）导致积分偏低，关系阶段计算错误 |
| **修复** | 在 `_restore_state_from_log` 中先查 `bond_stage_transition_log`，再查 `bond_update_log`，取最新的一条 `STAGE_TRANSITION` 记录作为恢复基准 |

---

**第25轮排查文件：12 个 | 累计已排查：约 192 个 | 剩余约 75 个**

*文档最后更新：第 25 轮排查完成*

---

#### 🔴 P1 — `core/qa.py`：`VALID_EMOTION_TAGS` 缺少 M28-L 新增的 8 种情感标签，合法 emotion_tag 被错误降级为 NEUTRAL

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/core/qa.py` `VALID_EMOTION_TAGS` |
| **根因** | `VALID_EMOTION_TAGS` 只包含 8 种情感（原有 6 种 + M26-E 补充 2 种），但 M28-L（OPT-34）在 `channels/vts.py` 和 `vts_mapping_store.py` 中新增了 8 种情感（`FRUSTRATED/ANXIOUS/LOVING/EMBARRASSED/CONFUSED/BORED/PROUD/GRATEFUL`）。`_validate_emotion_tag` 遇到这 8 种合法情感时，因不在 `VALID_EMOTION_TAGS` 中而将其降级为 `NEUTRAL` 并写 QA 警告日志 |
| **影响** | LLM 输出 M28-L 新增的 8 种情感时，表情驱动全部失效（降级为 NEUTRAL 表情），情感表达能力退化为 8 种，与 VTS 映射表不一致 |
| **修复** | 将 M28-L 新增的 8 种情感标签补充到 `VALID_EMOTION_TAGS` 中，与 `channels/vts.py EMOTION_TO_EXPRESSION_ID` 保持一致 |

---

**第26轮排查文件：16 个 | 累计已排查：约 208 个 | 剩余约 59 个**

*文档最后更新：第 26 轮排查完成*

---

#### 🔴 P1 — `governor/tokens.py`：`validate_execution_tokens` 中 approval_token 在 confirm 验证失败时已被消费无法回滚

| 字段 | 内容 |
|------|------|
| **文件** | `host_brain/governor/tokens.py` `TokenService.validate_execution_tokens` |
| **根因** | 原实现先以 `consume=True` 消费 `approval_token`，再验证 `confirm_token`。若 `confirm_token` 验证失败，`approval_token` 已被标记为 `used=True` 无法回滚，导致该 intent 无法重试（approval token 已失效，下次调用会返回 `TOKEN_INVALID: token already used`） |
| **影响** | 需要二次确认（`require_confirm=True`）的高风险 Skill（risk_level=4/5）在 confirm 验证失败后，用户无法重试，必须重新发起完整的 intent 流程 |
| **修复** | 修改为"先验证（不消费）再统一消费"策略：先以 `consume=False` 验证 approval_token，再以 `consume=False` 验证 confirm_token，两者均通过后才依次以 `consume=True` 消费两个 token |

---

**第27轮排查文件：16 个 | 累计已排查：约 224 个 | 剩余约 43 个**

*文档最后更新：第 27 轮排查完成*
