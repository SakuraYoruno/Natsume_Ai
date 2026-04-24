# 模板作用总表（2026-04-21）

## 概览

- 运行时 Prompt 模板总数：`180`
- `prompts/templates/agents.yaml`：`18`
- `prompts/templates/chat.yaml`：`82`
- `prompts/templates/executive.yaml`：`26`
- `prompts/templates/governance.yaml`：`3`
- `prompts/templates/persona.yaml`：`48`
- `prompts/templates/runtime.yaml`：`3`

说明：

- 下文“运行时 Prompt 模板”的作用主要来自模板自身 `description`
- `.zh` / `.ja` 镜像模板也单独列出，因为它们会被动态语言镜像逻辑消费
- “其他模板”包含配置示例、流程模板、脚手架模板和规范/迁移模板

## 运行时 Prompt 模板

### prompts/templates/agents.yaml

- `chat_agent_handler.runtime_texts`：ChatAgent 运行期摘要与异常摘要模板（JSON）
- `chat_handler_vision.fallback_texts`：视觉链兜底文案（JSON，含不可用/报错/签名失败/来源拒绝等）
- `chat_handler_vision.analysis_prompt.templates`：视觉链分析 prompt 模板（JSON，含 system prompt、user prompt、上下文字段标签）
- `multimodal_observer.model_prompt.templates`：多模态观察器模型提示模板（JSON，仅做附件理解，不直接产出用户可见回复）
- `emotion_agent_handler.error_summary`：EmotionAgent 异常摘要
- `emotion_agent_handler.prompt_templates`：EmotionAgent system prompt 模板（JSON）
- `intent_agent_entry_handler.failure_texts`：IntentAgentEntry 子图回退文案模板（JSON）
- `intent_agent_handler.error_summary`：IntentAgent 异常摘要
- `intent_agent_handler.system_prompt_extra`：IntentAgent ReAct 额外 system prompt
- `judge_agent_handler.prompt_templates`：JudgeAgent ReAct prompt 模板（JSON）
- `react_loop.prompt_templates`：ReAct 循环 prompt 与运行期文案模板（JSON）
- `repair_agent_handler.runtime_texts`：RepairAgent 运行期文案与 LLM 修复规划 prompt 模板（JSON）
- `retrieval_agent_handler.runtime_texts`：RetrievalAgent system prompt 与异常摘要模板（JSON）
- `sub_agent_protocol.runtime_texts`：SubAgentDispatcher runtime texts
- `sub_agent_protocol.agent_capabilities`：SubAgent 能力描述模板（JSON）
- `supervisor_dispatch_handler.prompt_templates`：SupervisorDispatch LLM 路由 prompt 模板（JSON）
- `translate_agent_handler.runtime_texts`：TranslateAgent system prompt 与异常摘要模板（JSON）
- `vision.action_planner.prompt_templates`：ActionPlanner prompt 模板（JSON，含 system prompt 与 user prompt）

### prompts/templates/chat.yaml

- `bond_aware_style.style_templates`：BondAwareStyle 关系风格模板（JSON）
- `chat_agent.minimal_fallback.codex_worker`：ChatAgent 最小兜底：Codex Runtime 代码技能结果未顺好
- `chat_agent.minimal_fallback.default`：ChatAgent 最小兜底：通用
- `chat_agent.minimal_fallback.greeting`：ChatAgent 最小兜底：问候
- `chat_agent.minimal_fallback.ja_default`：ChatAgent 最小兜底：日语默认回复
- `chat_agent.minimal_fallback.phatic_fastpath`：ChatAgent 最小兜底：寒暄快路由
- `chat_agent.minimal_fallback.presence`：ChatAgent 最小兜底：在线确认
- `chat_agent.minimal_fallback.prompt_build_failed`：ChatAgent prompt 构建失败时的最小兜底
- `chat_agent.minimal_fallback.recent_turn_direct`：ChatAgent 最小兜底：最近一轮直接回答重述
- `chat_agent.minimal_fallback.localized_texts`：ChatAgent 最小兜底双语文案（JSON）
- `chat_agent.response_policy.min_sentence_placeholder`：响应策略硬守卫：最小占位句
- `chat_agent.response_policy.confession_replacements`：响应策略硬守卫：告白类替换词典
- `chat_agent.response_policy.intimacy_replacements`：响应策略硬守卫：亲密称呼替换词典
- `chat_agent.response_policy.withdrawn_replacements`：响应策略硬守卫：收敛语气替换词典
- `chat_agent.sub_agent_context.prompt_templates`：子 Agent 上下文提示模板（JSON）
- `chat_agent.capabilities.reply_templates`：能力说明兜底模板（JSON）
- `chat_agent.hub_skill.reply_templates`：Hub 技能确定性渲染模板（JSON，支持 name、message、preview、repair_actions、total、query、registry_error、source_names）
- `chat_agent.plan_execution.reply_templates`：计划执行确定性渲染模板（JSON，支持 step_index、topic、remembered、description_or_target、error_text、recovery_text、total_steps）
- `chat_agent.runtime_texts`：ChatAgent 运行时零散默认文案模板（JSON）
- `chat_agent.system_skill.reply_templates`：system skill 确定性收口模板（JSON）
- `chat_agent.prefrontal_context_templates`：ChatAgent 前额叶规划上下文块模板（JSON）
- `chat_context.runtime_guidance_texts`：ChatContextBuilder 运行期人格引导文案模板（JSON，按中日文分流）
- `chat_handler.sys_mode.narratives`：ChatHandler 系统模式拦截文案（JSON，含 locked/maintenance）
- `cortex_prompt_builder.continuity_sections`：CortexPromptBuilder continuity 上下文块模板（JSON，含 User/Assistant 标签与上下文标题）
- `cortex_prompt_builder.sections`：CortexPromptBuilder system/prompt 分段标题模板（JSON，含角色人设/说话风格/用户消息）
- `cortex_prompt_builder.style_correction.cn`：CortexPromptBuilder 风格纠偏：中文
- `cortex_prompt_builder.style_correction.ja`：CortexPromptBuilder 风格纠偏：日文
- `expression_adapter.prompt_section.templates`：ExpressionAdapter 表达策略提示模板（JSON，含标题、标签与自然语言描述）
- `expression_adapter.style_hint.templates`：ExpressionAdapter 风格提示片段模板（JSON）
- `intelligent_runtime.sanitize.empty_narrative`：IntelligentRuntime 叙事净化后为空时的最小兜底文本
- `interaction_pattern_analyzer.runtime_texts`：InteractionPatternAnalyzer 运行时文案模板（JSON，含 keyword/empty/LLM 默认文本）
- `interaction_pattern_analyzer.analysis_prompt`：InteractionPatternAnalyzer 的 LLM 分析 prompt 模板
- `proactive_voice.holiday_templates`：PersonaProactiveVoice 节日专属与默认文案模板（JSON）
- `proactive_voice.morning_greet_templates`：PersonaProactiveVoice 四季早安文案模板（JSON）
- `proactive_voice.runtime_texts`：PersonaProactiveVoice 运行时散落文案模板（JSON）
- `proactive_voice.scene_templates`：PersonaProactiveVoice 主要场景文案模板（JSON）
- `prompt_builder.character_dynamic_patch.templates`：动态人设补丁模板（JSON，支持季节/节日/情绪补丁和标题）
- `prompt_builder.current_time_context.templates`：当前本地时间上下文模板（JSON，供快路与完整聊天统一注入）
- `prompt_builder.direct_response.affection_rules`：直接回答优先级：喜欢 / 告白类规则
- `prompt_builder.direct_response.base_rules`：直接回答优先级：基础规则
- `prompt_builder.direct_response.block_title`：直接回答优先级标题
- `prompt_builder.direct_response.brief_rule`：直接回答优先级：简短回复规则
- `prompt_builder.direct_response.close_followup_rule`：直接回答优先级：亲近阶段追问规则
- `prompt_builder.direct_response.daily_life_rule`：直接回答优先级：日常状态类问题规则
- `prompt_builder.direct_response.default_followup_rule`：直接回答优先级：默认追问规则
- `prompt_builder.direct_response.followup_hint_prefix`：直接回答优先级：追问建议前缀
- `prompt_builder.direct_response.ja_output_rule`：直接回答优先级：日语输出规则
- `prompt_builder.direct_response.priority_rule`：直接回答优先级：优先级请求规则
- `prompt_builder.direct_response.zh_output_rule`：直接回答优先级：中文输出规则
- `prompt_builder.direct_response.scenario_rules`：直接回答优先级：场景首句策略规则（JSON）
- `prompt_builder.emotion_binding.texts`：PromptBuilder 情绪绑定描述模板（JSON）
- `prompt_builder.host_generation.blocks`：Host 简版生成 prompt 模板（JSON，含输出契约、检索标签、空证据占位）
- `prompt_builder.injection_guard.templates`：PromptBuilder 注入防护提示模板（JSON，含安全告警文本）
- `prompt_builder.node_role.instructions`：节点角色指令模板（JSON，支持各节点角色说明与 tool_planner 技能列表模板）
- `prompt_builder.persona_expression.templates`：PromptBuilder 人格表达提示模板（JSON，含字段标签与 tone guidance）
- `prompt_builder.render_contract.blocks`：节点渲染输出契约模板（JSON，支持输出格式说明、核心规则、章节标题和标签）
- `prompt_builder.response_policy.directives`：响应策略转提示文本模板（JSON，支持约束提示与 bond 阶段说明）
- `prompt_builder.reunion.guidance`：PromptBuilder 重逢语境补丁（JSON，按 reunion 类型映射）
- `prompt_builder.runtime_sections.templates`：PromptBuilder 运行时 section 标题与标签模板（JSON，含 bond/planning/multimodal/memory recall 等块）
- `prompt_builder.self_reflection.templates`：PromptBuilder 自我反思提示模板（JSON，含叙事约束前缀与布尔标签）
- `prompt_context.emotion_pulse_texts`：PromptContext 情绪脉冲文案模板（JSON）
- `reflection_chain.affection_disclosure.close`：告白类输入：亲密阶段修正回复（中日双语）
- `reflection_chain.affection_disclosure.default`：告白类输入：默认修正回复（中日双语）
- `reflection_chain.affection_disclosure.friend`：告白类输入：朋友阶段修正回复（中日双语）
- `reflection_chain.affection_disclosure.stranger`：告白类输入：陌生/初识阶段修正回复（中日双语）
- `reflection_chain.affection_question.close`：喜欢类直问：亲密阶段修正回复（中日双语）
- `reflection_chain.affection_question.default`：喜欢类直问：默认修正回复（中日双语）
- `reflection_chain.affection_question.friend`：喜欢类直问：朋友阶段修正回复（中日双语）
- `reflection_chain.affection_question.stranger`：喜欢类直问：陌生/初识阶段修正回复（中日双语）
- `reflection_chain.contextual_fallback.default`：反思链最小兜底：通用（中日双语）
- `reflection_chain.contextual_fallback.greeting`：反思链最小兜底：问候（中日双语）
- `reflection_chain.contextual_fallback.presence`：反思链最小兜底：在线确认（中日双语）
- `reflection_chain.revision_empathy_prefix`：反思链负面情绪修正前缀（中日双语）
- `style_distiller.render_instruction.templates`：StyleDistiller 风格蒸馏渲染与描述模板（JSON）
- `bond_aware_style.style_templates.zh`：BondAwareStyle 中文镜像模板（按 canon 校准）
- `bond_aware_style.style_templates.ja`：BondAwareStyle 日文镜像模板（按 canon 校准）
- `expression_adapter.style_hint.templates.zh`：ExpressionAdapter 中文镜像模板（按 canon 校准）
- `expression_adapter.style_hint.templates.ja`：ExpressionAdapter 日文镜像模板（按 canon 校准）
- `expression_adapter.prompt_section.templates.zh`：ExpressionAdapter 中文 prompt section 镜像模板
- `expression_adapter.prompt_section.templates.ja`：ExpressionAdapter 日文 prompt section 镜像模板
- `intelligent_runtime.sanitize.empty_narrative.zh`：IntelligentRuntime 中文镜像最小兜底
- `intelligent_runtime.sanitize.empty_narrative.ja`：IntelligentRuntime 日文镜像最小兜底

### prompts/templates/executive.yaml

- `cortex_orchestrator.runtime_texts`：CortexOrchestrator 运行期快捷回复/委托整理/任务进度/最近一轮澄清文案模板（JSON）
- `executive_router.runtime_texts`：ExecutiveRouter 运行期任务进度/计划状态/委托与技能执行文案模板（JSON，中日双语）
- `executive_router.failure_recovery.templates`：ExecutiveRouter 失败恢复计划文案模板（JSON，含 summary 与 repair/intent task prompt）
- `executive_router.routing_prompt.templates`：ExecutiveRouter 路由分类 prompt 模板（JSON，含 system prompt、user prompt 与 fallback skill 列表）
- `plan_graph.synthesize.prompt_templates`：PlanGraph 多步骤结果综合节点 prompt 模板（JSON）
- `http_hooks_runtime_helpers.proactive_talk_llm_prompt`：主动问候 LLM prompt 模板（JSON）
- `input_routes.fallback_texts`：InputRoutes Gateway 回复兜底文案（JSON，中日双语）
- `input_service_quality.runtime_texts`：InputServiceQuality 执行纠偏运行时文案模板（JSON）
- `input_routes.multimodal_runtime_texts`：InputRoutes Gateway 多模态入站运行时文案（JSON，中日双语）
- `provider_factory.host_mock.reply_templates`：ProviderFactory Host Mock 文案模板（JSON）
- `provider_factory.host_openai.prompt_templates`：ProviderFactory Host OpenAI system prompt templates
- `thalamus_agent.intent_classification_prompts`：ThalamusAgent canonical intent classification prompt 模板
- `prefrontal_agent.plan_response.prompt`：PrefrontalAgent 前额叶意图规划 prompt 模板（Phase D，小模型低延迟调用，决策“我要做什么”）
- `language_system_agent.wernicke_deep_parse`：LanguageSystemAgent Wernicke 深度语义解析 prompt（对复杂意图做 LLM 槽位抽取）
- `language_system_agent.broca_opening_hint`：LanguageSystemAgent Broca 开场句建议 prompt（高情绪压力时生成具体开场句）
- `language_system_agent.runtime_texts`：LanguageSystemAgent 运行期语气提示模板（JSON）
- `cerebellum_agent.correction_narrative`：CerebellumAgent 纠偏叙事 prompt（执行失败纠偏时生成面向用户的自然语言解释，注入 ChatAgent extra_context）
- `cerebellum_agent.failure_pattern`：CerebellumAgent 失败模式分析 prompt（连续失败时分析失败模式，产出 failure_insight 写入语义记忆）
- `peripheral_agent.context_analysis`：PeripheralAgent 跨平台情境感知 prompt（分析多平台连接状态的语义含义，注入 PrefrontalAgent context 字段）
- `motor_cortex_agent.speak_style`：MotorCortexAgent 发声风格微调 prompt（dispatch_speak 前调用 LLM 生成语速/停顿/语气词建议，注入 TTS prosody_hint）
- `input_service_quality.runtime_texts.zh`：InputServiceQuality 中文镜像运行时文案
- `input_service_quality.runtime_texts.ja`：InputServiceQuality 日文镜像运行时文案
- `provider_factory.host_mock.reply_templates.zh`：ProviderFactory Host Mock 中文镜像文案
- `provider_factory.host_mock.reply_templates.ja`：ProviderFactory Host Mock 日文镜像文案
- `executive_router.failure_recovery.templates.zh`：ExecutiveRouter 中文镜像失败恢复文案
- `executive_router.failure_recovery.templates.ja`：ExecutiveRouter 日文镜像失败恢复文案

### prompts/templates/governance.yaml

- `core.qa.fallback_texts`：Core QA 情感感知降级文案（JSON）
- `immune_agent.runtime_rules`：ImmuneAgent 可运营规则覆盖（关键词/正则）
- `immune_agent.semantic_intent_analysis`：ImmuneAgent 语义意图分析 LLM prompt 模板（对规则层灰色地带输入做二次语义扫描）

### prompts/templates/persona.yaml

- `body_sense.style_hints`：BodySense 身体感知风格提示模板（JSON）
- `drift_detector.correction_hints`：PersonaDriftDetector 校正文案模板（JSON，含按维度提示与通用兜底）
- `emotion_context.inner_monologue_texts`：EmotionContext VAD 映射到内心独白的文案模板（JSON）
- `emotion_context.user_profile_hints`：EmotionContext 用户情感画像提示模板（JSON）
- `emotion_llm_inference.prompt_templates`：Emotion LLM 推理 prompt 模板（JSON）
- `emotion_utterance_index.prompt_render_texts`：EmotionUtteranceIndex 风格参考渲染模板（JSON）
- `emotional_silence.runtime_hints`：EmotionalSilence 沉默/抗拒提示模板（JSON）
- `evolution_desire_engine.desire_templates`：EvolutionDesireEngine 成长意愿模板（JSON）
- `evolution_orchestrator.awareness_templates`：EvolutionOrchestrator 成长感知文案模板（JSON）
- `proactive_voice.runtime_texts`：PersonaProactiveVoice 运行时文案模板（JSON）
- `proactive_voice.scene_templates`：PersonaProactiveVoice 主动表达场景模板（JSON）
- `proactive_voice.morning_greet_templates`：PersonaProactiveVoice 早安场景模板（JSON）
- `proactive_voice.holiday_templates`：PersonaProactiveVoice 节日场景模板（JSON）
- `inner_world.runtime_texts`：InnerWorld 离线内心文案模板（JSON）
- `inner_activity_agent.prompt_templates`：InnerActivityAgent 内心活动生成 prompt 模板（JSON）
- `persona.self_narrative_prompts`：Persona 自我叙事/成长意愿 LLM prompt 模板（JSON）
- `self_model.narrative_text_pools`：SelfModel 自省回退文本池模板（JSON）
- `emergent_growth_engine.narrative_templates`：EmergentGrowthEngine 成长叙事文案池（JSON）
- `self_model.state_alignment_texts`：SelfModel 状态对齐自我感知文案模板（JSON）
- `self_reflection.narrative_guardrail_texts`：SelfReflection 自我叙事 guardrail 文案模板（JSON）
- `self_reflection.style_trend_templates`：SelfReflection 风格演变文案模板（JSON）
- `self_reflection.trigger_reason_templates`：SelfReflection 触发原因文案模板（JSON）
- `trait_drift_guard.trend_lock_templates`：TraitDriftGuard 趋势锁文案模板（JSON）
- `mirror_neuron_agent.empathy_response_hint`：MirrorNeuronAgent 共情回应建议 prompt（共鸣强度高时生成共情策略，注入 PrefrontalAgent tone_hint）
- `glymphatic_agent.consolidation_summary`：GlymphaticAgent 记忆巩固摘要 prompt（恢复窗口期调用 LLM 对当天情节记忆做语义压缩，模拟睡眠期记忆巩固）
- `initiative_governor_agent.proactive_topic`：InitiativeGovernorAgent 主动发声话题建议 prompt（Governor 决策 ALLOW 时生成话题种子，传给 DefaultModeAgent）
- `pain_reward_agent.hedonic_reflection`：PainRewardAgent 享乐状态反思 prompt（享乐状态显著变化时生成内心感受叙事，写入 DefaultModeAgent 独白素材池）
- `endocrine_agent.hormonal_state_hint`：EndocrineAgent 激素状态身体感受提示 prompt（将激素状态转化为自然语言身体感受，注入 Prompt Phase XIII）
- `circadian_agent.circadian_mood_hint`：CircadianAgent 昼夜节律情绪提示 prompt（根据昼夜相位和能量水平生成时段感知叙事，注入 Prompt Phase XIII）
- `proprioception_agent.cognitive_load_hint`：ProprioceptionAgent 认知负荷感知提示 prompt（认知负荷高时生成感知叙事，注入 Prompt Phase XIII）
- `interoception_agent.interoceptive_narrative`：InteroceptionAgent 内感受叙事 prompt（将内感受指数转化为身体感受叙事，注入 Prompt Phase XIII）
- `vestibular_agent.stability_assessment`：VestibularAgent 稳定性评估 prompt（稳定性异常时调用 LLM 分析不稳定原因，注入 PrefrontalAgent）
- `somatic_agent.somatic_narrative`：SomaticAgent 躯体感受叙事 prompt（将躯体信号转化为自然语言身体感受叙事，注入 Prompt Phase XIII）
- `proactive_voice.runtime_texts.zh`：PersonaProactiveVoice 中文镜像运行时文案（按 canon 校准）
- `proactive_voice.runtime_texts.ja`：PersonaProactiveVoice 日文镜像运行时文案（按 canon 校准）
- `proactive_voice.scene_templates.zh`：PersonaProactiveVoice 中文镜像场景模板（按 canon 校准）
- `proactive_voice.scene_templates.ja`：PersonaProactiveVoice 日文镜像场景模板（按 canon 校准）
- `proactive_voice.morning_greet_templates.zh`：PersonaProactiveVoice 中文镜像早安模板（按 canon 校准）
- `proactive_voice.morning_greet_templates.ja`：PersonaProactiveVoice 日文镜像早安模板（按 canon 校准）
- `proactive_voice.holiday_templates.zh`：PersonaProactiveVoice 中文镜像节日模板（按 canon 校准）
- `proactive_voice.holiday_templates.ja`：PersonaProactiveVoice 日文镜像节日模板（按 canon 校准）
- `persona.self_narrative_prompts.ja`：Persona 自我叙事/成长意愿 LLM prompt 模板（日文镜像）
- `self_model.narrative_text_pools.ja`：SelfModel 自省回退文本池模板（日文镜像）
- `emergent_growth_engine.narrative_templates.ja`：EmergentGrowthEngine 成长叙事文案池（日文镜像）
- `self_model.state_alignment_texts.ja`：SelfModel 状态对齐自我感知文案模板（日文镜像）
- `self_reflection.narrative_guardrail_texts.ja`：SelfReflection 自我叙事 guardrail 文案模板（日文镜像）
- `self_reflection.style_trend_templates.ja`：SelfReflection 风格演变文案模板（日文镜像）
- `self_reflection.trigger_reason_templates.ja`：SelfReflection 触发原因文案模板（日文镜像）

### prompts/templates/runtime.yaml

- `fastapi_app.runtime_texts`：FastAPI 主入口运行时文案模板（JSON）
- `failure_matrix.user_responses`：FailureMatrix 标准化用户响应文本模板（JSON）
- `default_mode_agent.runtime_texts`：DefaultModeAgent 运行时文案模板（JSON）

## 其他模板

- `.env.example`：仓库总环境变量示例与降级回退模板，覆盖 Host / Edge / 外部服务等配置
- `.env.core.example`：最小启动所需核心环境变量模板，面向新人和 MVP 启动
- `.env.optional.example`：可选集成与增强能力环境变量模板
- `.env.experimental.example`：实验性特性开关与灰度配置模板
- `configs/secrets/key_profiles.example.json`：密钥画像示例模板，供运行时检查与降级读取
- `configs/secrets/secret_store.runtime.example.json`：运行期 secret store 示例模板，供本地检查、演练和降级读取
- `host_brain/skills/custom_providers/template_provider.py.example`：自定义 skill provider 脚手架模板
- `docs/50_project/guides/DEV_TEST_REPORT_TEMPLATE.md`：开发节点提交“开发实测报告”的文档模板
- `docs/50_project/guides/RUNTIME_FEEDBACK_REPORT_TEMPLATE.md`：运行节点回传“运行反馈报告”的文档模板
- `prompts/llm_contract/refusal_templates.md`：QA 降级/拒绝叙事的规范模板说明
- `scripts/migrate_prompt_templates_to_yaml.py`：历史 prompt 模板迁移脚本，用于把旧 Python 定义导出到 YAML
