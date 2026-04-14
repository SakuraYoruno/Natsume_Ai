# QGBT240414R 子智能体 Gateway 注入修复留痕

- 日期: 2026-04-14
- 目标: 修复 `delegate -> sub_agent` 链路里“当前没有可用的 LLM gateway”的误降级问题。

## 根因确认

- `react_loop` 在 `gateway is None` 时会直接走 `NO_LLM` 降级分支。
- `code_agent/intent_agent` 的 handler 依赖 `context/payload` 里存在 `gateway`。
- 但 `executive_runtime_core -> sub_agent_protocol` 的调度链路此前未注入 gateway，导致子智能体总是拿到 `None`。

## 修复内容

1. 在 `SubAgentDispatcher.dispatch()` 增加运行时 gateway 注入逻辑：
   - 若上下文已有 gateway，则直接复用。
   - 若没有，则按当前 session/request 动态解析 host/subagent LLM runtime 并构造 gateway 注入。
2. 在 `_dispatch_to_sub_agent()` 传入可配置调度超时：
   - 新增 `HOST_SUBAGENT_DISPATCH_TIMEOUT_MS`（默认 90000ms）。
3. 工具链 `dispatch_sub_agent` 同步使用该默认超时，减少 30s 误超时。
4. `.env.example` 补充 `HOST_SUBAGENT_DISPATCH_TIMEOUT_MS` 注释。
5. `SubAgentGatewayAdapter` 增加 fallback 重试：
   - 主模型失败后按 `HOST_SUBAGENT_FALLBACK_MODEL_IDS`（默认 `gemini-2.5-flash-lite,gemini-2.5-flash`）重试。
   - Gemini fallback 会重新取路由 key，尽量绕开瞬时异常 key/model 组合。

## 验证结果（修复前后对比）

### 修复前

- request_id: `req_e21bb568f9e94b00822aa018b414afe5`
- route: `delegate -> code_agent`
- `skill_exec_log.result_digest`:
  - `[ReAct 降级] agent_id=code_agent，当前没有可用的 LLM gateway`

### 修复后

- request_id: `req_25ff898637a14186b99bcf945f002ea5`
- route: `delegate -> code_agent`
- `skill_exec_log.result_digest`:
  - 不再出现 `没有可用的 LLM gateway`
  - 变为上游模型瞬时错误：`503 high demand`

### 修复后（fallback 生效复测）

- request_id: `req_93d190e7f22f4263a92b7c1ffc7084af`
- route: `delegate -> code_agent`
- `skill_exec_log.result_digest`:
  - 不包含 `没有可用的 LLM gateway`
  - 不包含 `503`
  - 返回真实分析内容（非 NO_LLM 降级文本）

## 结论

- “LLM gateway 不可用”问题已修复（链路注入生效）。
- 当前剩余问题是模型服务侧的瞬时负载 (`503`)，不再是本地 gateway wiring 缺失。

## 关联文件

- `host_brain/app/sub_agent_protocol.py`
- `host_brain/app/executive_runtime_core.py`
- `host_brain/app/agents/tool_registry.py`
- `host_brain/app/agent_runtime_shared.py`
- `.env.example`
