受众：CODEX
标题：模板资产审计（2026-04-21）

# 模板资产审计（2026-04-21）

## 1. 审计范围

本次只审计仓库内明确以“模板 / example / report template”存在的资产，分为四类：

1. `prompts/templates/*.yaml` 运行时 Prompt 模板
2. 根目录与 `configs/secrets/` 下的配置示例模板
3. `docs/50_project/guides/` 下的流程报告模板
4. 迁移 / 脚手架类模板文件

审计方法：

- 先盘点模板文件和 `template_id`
- 再核对代码、测试、脚本、流程文档中的实际消费路径
- 对 `.zh` / `.ja` 镜像模板额外检查动态语言镜像逻辑，避免把“动态使用”误判为死模板

---

## 2. 总体结论

### 2.1 明确认定“有实际价值”的模板

以下模板目前仍有明确价值，不建议按“废模板”处理：

- `prompts/templates/*.yaml` 中绝大多数运行时模板
  - 多数直接被 `resolve_prompt_template_*()` 或 `_resolve_template_payload_with_mirror()` 消费
  - 本地化镜像模板虽然没有字面量引用，但会通过动态语言镜像逻辑被加载
- `.env.example`、`.env.core.example`、`.env.optional.example`、`.env.experimental.example`
  - 用于新人启动、降级回退和分层配置说明
- `configs/secrets/key_profiles.example.json`
- `configs/secrets/secret_store.runtime.example.json`
- `host_brain/skills/custom_providers/template_provider.py.example`
  - 被 `host_brain/skills/custom_provider_installer.py` 和相关文档直接消费
- `docs/50_project/guides/DEV_TEST_REPORT_TEMPLATE.md`
- `docs/50_project/guides/RUNTIME_FEEDBACK_REPORT_TEMPLATE.md`
  - 被 `docs/50_project/guides/DUAL_NODE_DEV_WORKFLOW.md` 明确指定为流程模板
- `prompts/llm_contract/refusal_templates.md`
  - 更偏规范文档，不是运行时模板，但仍被 DevSpec/DevTasks 引用

### 2.2 运行时 Prompt 模板的静态结果

- 当前 `prompts/templates/*.yaml` 共扫描到 `181` 个 `template_id`
- 其中 `27` 个在代码里没有“字面量引用”
- 这 `27` 个里有 `21` 个属于 `.zh` / `.ja` 镜像模板或语言分支模板，仍会被 `_resolve_template_payload_with_mirror()` 动态消费，不应误删
- 最终剩余 `6` 个可以认定为“当前没有实际运行价值”或“价值极低”的候选项

---

## 3. 确认低价值 / 无实际价值候选

### 3.1 `chat_agent.minimal_fallback.ja_default`

- 位置：`prompts/templates/chat.yaml`
- 结论：可删
- 原因：
  - 代码实际走的是 `chat_agent.minimal_fallback.localized_texts`
  - 旧回退链只保留 `chat_agent.minimal_fallback.default`
  - `chat_agent.minimal_fallback.ja_default` 没有任何代码、测试或脚本引用
  - 语义上已被 `default` 的 `ja` 分支覆盖，属于重复模板

### 3.2 `cortex_prompt_builder.continuity_sections`

- 位置：`prompts/templates/chat.yaml`
- 结论：可删
- 原因：
  - 没有任何代码或测试引用该 `template_id`
  - 当前连续上下文拼装已由 `host_brain/conversation/render.py` 的消息构建逻辑直接处理
  - 该模板看起来是旧版 “continuity 文本块” 方案遗留

### 3.3 `executive_router.runtime_texts`

- 位置：`prompts/templates/executive.yaml`
- 结论：建议删除或迁出归档
- 原因：
  - 没有任何运行时代码或测试引用
  - 当前任务进度 / 运行态文案实际由 `cortex_orchestrator.runtime_texts` 与 `host_brain/app/agent_runtime_shared.py` 承担
  - 内容量很大，但未进入真实调用链，维护成本高于收益

### 3.4 `executive_router.failure_recovery.templates`

- 位置：`prompts/templates/executive.yaml`
- 结论：建议删除或迁出归档
- 原因：
  - 没有任何运行时代码或测试引用
  - 当前 `host_brain/app/executive_runtime_core.py` 中的 `_build_failure_recovery_plan()` 只返回路由元数据，不消费该模板
  - 说明模板意图和当前实现已经脱节

### 3.5 `executive_router.failure_recovery.templates.zh`

- 位置：`prompts/templates/executive.yaml`
- 结论：建议和 3.4 一起删除
- 原因：
  - 作为 `executive_router.failure_recovery.templates` 的镜像模板存在
  - 但基础模板本身未进入调用链，镜像模板也随之失去价值

### 3.6 `executive_router.failure_recovery.templates.ja`

- 位置：`prompts/templates/executive.yaml`
- 结论：建议和 3.4 一起删除
- 原因：
  - 同上，属于未接入实现的镜像模板

---

## 4. 低价值但不建议立即删的文件

### 4.1 `scripts/migrate_prompt_templates_to_yaml.py`

- 结论：当前价值很低，但是否删除要看是否还想保留迁移历史工具
- 原因：
  - 这是一次性迁移脚本
  - 现在模板源已经是 YAML，本脚本的“迁移目标”已经完成
  - 继续保留的价值主要是历史参考，而不是现网能力

建议：

- 如果仓库强调“只保留现役工具”，可以删除
- 如果仓库强调“保留一次性迁移轨迹”，可以移到 `scripts/archive/` 或 `docs/35_quality/` 下说明用途

---

## 5. 不要误删的动态镜像模板

以下模板在静态 grep 中会显示“零引用”，但仍有实际价值，因为它们通过语言镜像逻辑动态加载：

- `bond_aware_style.style_templates.zh`
- `bond_aware_style.style_templates.ja`
- `expression_adapter.style_hint.templates.zh`
- `expression_adapter.style_hint.templates.ja`
- `expression_adapter.prompt_section.templates.zh`
- `expression_adapter.prompt_section.templates.ja`
- `intelligent_runtime.sanitize.empty_narrative.zh`
- `intelligent_runtime.sanitize.empty_narrative.ja`
- `input_service_quality.runtime_texts.zh`
- `input_service_quality.runtime_texts.ja`
- `provider_factory.host_mock.reply_templates.zh`
- `provider_factory.host_mock.reply_templates.ja`
- `proactive_voice.runtime_texts.zh`
- `proactive_voice.runtime_texts.ja`
- `proactive_voice.scene_templates.zh`
- `proactive_voice.scene_templates.ja`
- `proactive_voice.morning_greet_templates.zh`
- `proactive_voice.morning_greet_templates.ja`
- `proactive_voice.holiday_templates.zh`
- `proactive_voice.holiday_templates.ja`
- `persona.self_narrative_prompts.ja`

这些模板主要经由以下逻辑被动态消费：

- `host_brain/app/prompt_builder_utils.py`
  - `_resolve_template_payload_with_mirror()`
  - `_resolve_localized_template_payload()`
- `host_brain/persona/proactive_voice.py`
- `host_brain/persona/expression_adapter.py`
- `host_brain/persona/bond_aware_style.py`
- `host_brain/app/input_service_quality.py`
- `host_brain/app/provider_factory.py`

---

## 6. 建议的清理顺序

推荐按低风险到高风险处理：

1. 删除 `chat_agent.minimal_fallback.ja_default`
2. 删除 `cortex_prompt_builder.continuity_sections`
3. 删除 `executive_router.runtime_texts`
4. 删除 `executive_router.failure_recovery.templates` 及其 `.zh` / `.ja` 镜像
5. 将 `scripts/migrate_prompt_templates_to_yaml.py` 迁到归档目录或删除

如果后续要继续整理模板，下一步建议补一个自动化检查：

- 扫描 `template_id` 是否存在实际调用
- 对 `.zh` / `.ja` 镜像模板识别动态消费路径
- 区分“运行时模板”“人工流程模板”“历史迁移模板”

