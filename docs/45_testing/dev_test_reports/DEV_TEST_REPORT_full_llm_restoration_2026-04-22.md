受众：MASTER
标题：DEV 实测报告：全认知能力 LLM 恢复（2026-04-22）

版本：v1.0
日期：2026-04-22
关联文档：`docs/50_project/arch/NATSUME_AI_FULL_LLM_CAPABILITY_RESTORATION_PLAN_20260422.md`

## 1. 本次 DEV 范围

- 共享模型池：tier A/B/C/D、MiniMax provider、冷却、缓存、`LLM_POOL_EXHAUSTED`
- 主链接池：`chat_agent`、`thalamus`、`emotion_llm_inference`、`multimodal_observer_backends`
- 认知模块接池：`reflection_chain`、`self_reflection`、`evolution_desire_engine`、`bond_narrative`、`style_distiller`
- 内心世界：`inner_world_numeric_tick` / `inner_world_monologue_tick`
- 记忆层：`hippocampus` 联想摘要、`MemoryDistillationEngine`、`LongTermMemoryDistiller`
- 调度治理：后台 LLM 任务 `sys_mode` 门控、`LLM_POOL_EXHAUSTED -> SKIPPED`
- CI 门禁：`scripts/ci/check_no_rule_degrade.py` + `tools/static_scan/run_all.py`

## 2. 已执行验证

- `pytest tests/unit/test_memory_pool_summary_client.py tests/unit/test_hippocampus_agent.py tests/unit/test_scheduler_engine.py tests/contract/test_llm_restoration_contract.py -q`
  结果：`26 passed`
- `pytest tests/unit/test_memory_distillation.py tests/unit/test_long_term_distiller.py tests/unit/test_distiller_emotion.py tests/unit/test_motive_runtime_background.py tests/unit/test_prompt_template_store.py -q`
  结果：`64 passed`
- `pytest tests/unit/test_http_hooks_startup_status.py tests/unit/test_inner_world_skill_tribe_events.py tests/unit/test_prefrontal_agent.py tests/unit/test_mirror_neuron_agent.py tests/unit/test_initiative_governor_agent.py -q`
  结果：`29 passed`
- `pytest tests/unit/test_self_reflection_helpers.py tests/unit/test_evolution_desire_engine_helpers.py tests/unit/test_interaction_pattern_analyzer.py tests/contract/test_ci_gate_contract.py -q`
  结果：本轮收口补测，覆盖 strict-LLM 与 CI 门禁
- 语法检查：`py_compile` 覆盖本轮新增/修改的 Python 文件，通过
- Monitor UI：此前已执行 `npm run build`，静态资源已刷新到 `host_brain/app/static/monitor/`

## 3. 当前结论

- 共享池、观测、错误码、MiniMax provider、主链接池和主要认知模块接池已落库
- 后台 LLM 任务已具备模式门控与池耗尽跳过策略
- `emergent_growth_weekly`、`self_reflection` 等运行时主路径已收紧为“无 LLM 不静默降级”
- CI 已新增禁降级扫描脚本，并纳入静态扫描聚合器

## 4. 尚待运行节点验证

- 真机/长时运行验证：`inner_stream` 5 分钟独白、全天 `llm_pool_call` 观测、Gemini 故障切 MiniMax
- 真机 cron 验证：`emergent_growth_weekly`
- 真机对话验证：多轮反思承接、sys_mode=focus 的实际运行态拦截
