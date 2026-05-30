受众：CODEX
标题：DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE_BATCH_B_20260409

# DIGITAL_LIFE_P7_ORGAN_LOOP_CLOSURE_BATCH_B_20260409

## 目标
- 在 Batch A 的基础上，把 `ImmuneAgent` 从“硬编码规则”推进到“可运营可审计”。

## 本批完成项
- `ImmuneAgent` 新增模板化规则加载：
  - 模板 ID：`immune_agent.runtime_rules`
  - 支持 `keyword_rules` / `regex_rules` 两类规则（可通过 PromptTemplateStore 覆盖）。
  - 支持缓存 TTL：`HOST_IMMUNE_TEMPLATE_CACHE_TTL_MS`。
  - 文件：`host_brain/tribe/immune_agent.py`
- 新增模板定义：
  - 文件：`host_brain/app/prompt_template_defs.py`
  - 分类：`governance`
  - 作用域：`immune_agent`
- `/v1/chat` 增加 immune 审计落账：
  - 事件：`IMMUNE_CHAT_GATE`
  - stage：`chat.immune_gate`
  - 落账表：`voice_event_log`
  - 文件：`host_brain/app/fastapi_app.py`
- `/v1/host/input` 的 immune 阻断增加审计落账：
  - 阻断时写 `INPUT_REJECTED`，`reason_code=IMMUNE_BLOCKED_HIGH_RISK`
  - 文件：`host_brain/app/input_routes.py`

## 验证记录
- 新增测试：
  - `tests/unit/test_immune_agent_templates.py`
  - `tests/unit/test_fastapi_app_immune_gate.py`（追加审计落账断言）
- 预期关键断言：
  - 模板规则可被加载并生效（`template_keyword`/`template_regex` 来源可见）。
  - `/v1/chat` immune 决策会落到 `voice_event_log` 的 `IMMUNE_CHAT_GATE` 事件。
  - `/v1/host/input` immune BLOCK 会被拒绝并产生日志事件。

## 下一步（Batch C）
- 把 immune 规则编辑能力接入 WebUI 专页（非仅模板列表页）。
- 把 immune 决策纳入 Trace 事件流节点展示（与 query planner / dispatch / sub-agent 对齐）。
- 增加“误拦截回放”工具链（按 request_id 回放文本 + 规则命中详情）。
